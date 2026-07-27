"""微信消息异步处理 — 更新互动状态 → 匹配线索 → 自动发送"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import (
    CommentLead, ContactDelivery, ContactPackage,
    ConversationMessage, WeChatUserInteraction,
)
from app.services.wechat_callback_service import normalize_keyword

logger = logging.getLogger(__name__)

# 会话窗口时长（集中配置）
SESSION_WINDOW_HOURS = 48


def update_user_interaction(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    message_type: str,
    is_following: Optional[bool] = None,
    event_type: Optional[str] = None,
):
    """更新用户互动状态"""
    now = datetime.now(timezone.utc)

    interaction = db.query(WeChatUserInteraction).filter(
        WeChatUserInteraction.account_id == account_id,
        WeChatUserInteraction.openid == openid,
    ).first()

    if not interaction:
        interaction = WeChatUserInteraction(
            tenant_id=tenant_id,
            account_id=account_id,
            openid=openid,
        )
        db.add(interaction)

    # 关注/取消关注事件
    if is_following is not None:
        interaction.is_following = is_following
        if is_following:
            interaction.follow_time = now
        else:
            interaction.unfollow_time = now

    # 用户主动消息更新互动时间
    if message_type in ("text", "image", "voice", "video", "location", "link"):
        interaction.last_inbound_at = now
        if message_type == "text":
            interaction.last_text_at = now

        # 更新会话窗口（用户主动消息开启 48h 窗口）
        interaction.session_status = "active"
        interaction.session_started_at = now
        interaction.session_expires_at = now + timedelta(hours=SESSION_WINDOW_HOURS)

    # 事件时间
    if event_type:
        interaction.last_event_at = now

    db.commit()


def process_incoming_message(message_id: int):
    """处理单条回调消息

    由 Celery 异步调用，不可在 callback HTTP 中同步执行。

    流程:
    1. 标记 processing
    2. 更新互动状态
    3. 标准化关键词
    4. 匹配 lead
    5. 检查自动发送条件
    6. 创建 delivery
    7. 标记 processed
    """
    from app.database import MysqlSessionLocal

    db = MysqlSessionLocal()
    try:
        msg = db.query(ConversationMessage).filter(
            ConversationMessage.id == message_id,
        ).first()
        if not msg:
            logger.warning("Message %d not found", message_id)
            return
        if msg.processing_status != "received":
            logger.info("Message %d already %s, skip", message_id, msg.processing_status)
            return

        # 1. 标记 processing
        msg.processing_status = "processing"
        db.commit()

        # 2. 更新互动状态
        is_following = None
        if msg.event_type == "subscribe":
            is_following = True
        elif msg.event_type == "unsubscribe":
            is_following = False

        update_user_interaction(
            db, msg.tenant_id, msg.account_id, msg.openid,
            msg.message_type,
            is_following=is_following,
            event_type=msg.event_type,
        )

        # 3. 非文本消息直接标记为 ignored
        if msg.message_type != "text":
            msg.processing_status = "ignored"
            db.commit()
            logger.info("Message %d: non-text type=%s, ignored", message_id, msg.message_type)
            return

        # 4. 标准化关键词
        raw_keyword = msg.content or ""
        normalized = normalize_keyword(raw_keyword)
        if not normalized:
            msg.processing_status = "ignored"
            db.commit()
            return

        # 5. 匹配 lead
        lead = _match_lead(db, msg.account_id, msg.openid, normalized)
        if not lead:
            msg.processing_status = "ignored"
            msg.matched_lead_id = None
            db.commit()
            logger.info("Message %d: no matching lead for keyword '%s'", message_id, normalized[:20])
            return

        msg.matched_lead_id = lead.id
        db.commit()

        # 6. 检查自动发送条件
        if lead.status == "manual_review":
            msg.processing_status = "manual_review_required"
            db.commit()
            return

        if not lead.auto_send_on_message:
            msg.processing_status = "ignored"
            logger.info("Message %d: lead %d auto_send disabled", message_id, lead.id)
            db.commit()
            return

        pkg_id = lead.auto_send_package_id
        if not pkg_id:
            msg.processing_status = "manual_review_required"
            lead.status = "manual_review"
            db.commit()
            return

        pkg = db.query(ContactPackage).filter(
            ContactPackage.id == pkg_id,
            ContactPackage.tenant_id == msg.tenant_id,
            ContactPackage.deleted_at.is_(None),
        ).first()
        if not pkg or not pkg.is_enabled:
            msg.processing_status = "manual_review_required"
            lead.status = "manual_review"
            db.commit()
            logger.info("Message %d: package %d not available", message_id, pkg_id)
            return

        # 7. 幂等键
        idempotency_key = f"incoming:{message_id}:lead:{lead.id}:package:{pkg_id}"

        # 检查是否已有 delivery
        existing_delivery = db.query(ContactDelivery).filter(
            ContactDelivery.tenant_id == msg.tenant_id,
            ContactDelivery.idempotency_key == idempotency_key,
        ).first()
        if existing_delivery:
            msg.delivery_id = existing_delivery.id
            msg.processing_status = "processed"
            lead.status = "contact_sent"
            db.commit()
            logger.info("Message %d: delivery %d already exists", message_id, existing_delivery.id)
            return

        # 8. 资格检查（仅在 live 模式需要）
        from app.services.wechat_eligibility_service import check_contact_eligibility
        try:
            eligibility = await_eligibility_check(db, msg.tenant_id, msg.account_id, lead.openid)
            if not eligibility.eligible:
                msg.processing_status = "manual_review_required"
                lead.status = "manual_review"
                db.commit()
                logger.info("Message %d: lead %d ineligible: %s", message_id, lead.id, eligibility.reason_code)
                return
        except Exception as e:
            logger.warning("Message %d: eligibility check failed: %s", message_id, e)
            # mock 模式下忽略资格错误

        # 9. 创建 delivery
        try:
            delivery = _create_delivery_for_lead(
                db, lead, pkg, idempotency_key, msg.tenant_id, msg.account_id,
            )
            msg.delivery_id = delivery.id
            msg.processing_status = "processed"
            lead.status = "contact_sent"
            db.commit()

            # 10. 投递异步发送
            _dispatch_delivery(delivery.id)
            logger.info("Message %d: delivery %d created for lead %d",
                        message_id, delivery.id, lead.id)

        except Exception as e:
            msg.processing_status = "failed"
            msg.processing_error = str(e)
            db.commit()
            raise

    except Exception as exc:
        try:
            msg = db.query(ConversationMessage).filter(
                ConversationMessage.id == message_id,
            ).first()
            if msg and msg.processing_status != "processed":
                msg.processing_status = "failed"
                msg.processing_error = str(exc)
                db.commit()
        except Exception:
            pass
        logger.error("Process message %d failed: %s", message_id, exc)
        raise
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _match_lead(db: Session, account_id: int, openid: str, normalized_keyword: str) -> Optional[CommentLead]:
    """匹配 lead

    优先级:
    1. 同一 account_id + openid
    2. status = awaiting_user_message
    3. guide_keyword_normalized 精确匹配
    4. guide_sent_at 最近优先

    如果多条 lead 同时匹配 → 返回 None（manual_review）
    """
    leads = db.query(CommentLead).filter(
        CommentLead.account_id == account_id,
        CommentLead.openid == openid,
        CommentLead.status == "awaiting_user",
        CommentLead.guide_keyword_normalized == normalized_keyword,
    ).order_by(CommentLead.guide_sent_at.desc()).all()

    if len(leads) == 1:
        return leads[0]

    if len(leads) > 1:
        logger.warning("Multiple leads match account=%d openid=%s keyword=%s, count=%d",
                       account_id, openid[:8], normalized_keyword[:20], len(leads))
        # 标记所有匹配 lead 为 manual_review
        for ld in leads:
            ld.status = "manual_review"
        db.commit()
        return None

    return None


def _create_delivery_for_lead(
    db: Session,
    lead: CommentLead,
    pkg: ContactPackage,
    idempotency_key: str,
    tenant_id: int,
    account_id: int,
) -> ContactDelivery:
    """为线索创建发送任务"""
    from app.services.wechat_delivery_service import create_delivery

    delivery = create_delivery(
        db=db,
        tenant_id=tenant_id,
        lead_id=lead.id,
        account_id=account_id,
        openid=lead.openid,
        package_id=pkg.id,
        operator_id=None,
        idempotency_key=idempotency_key,
    )
    return delivery


def _dispatch_delivery(delivery_id: int):
    """投递发送任务（尝试 Celery，回退线程）"""
    import threading
    from app.services.wechat_delivery_service import execute_delivery
    import asyncio

    t = threading.Thread(target=lambda: asyncio.run(execute_delivery(delivery_id)), daemon=True)
    t.start()


def await_eligibility_check(db, tenant_id, account_id, openid):
    """同步包装的资格检查"""
    import asyncio
    from app.services.wechat_eligibility_service import check_contact_eligibility

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            check_contact_eligibility(db, tenant_id, account_id, openid, force_refresh=True)
        )
        return result
    finally:
        loop.close()
