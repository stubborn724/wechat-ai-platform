"""评论自动转化服务。

职责是把“评论线索”推进到下一步转化动作：
1. 若用户具备私信资格，优先自动发送联系方式资料包与二维码；
2. 若当前不具备私信资格，则自动公开回评并引导用户发送关键词；
3. 所有动作都要回写线索状态，保证后续工作台可追踪、可重试。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import CommentLead, ContactPackage, WeChatComment

logger = logging.getLogger(__name__)

AUTO_GUIDE_KEYWORD = "资料"


def _pick_enabled_package(db: Session, tenant_id: int, account_id: int) -> Optional[ContactPackage]:
    """挑选一个可用的联系方式资料包。

    设计上优先使用已配置的默认资料包，其次退化到任意启用中的资料包，
    这样自动化链路不会因为少配一个默认项就整体失效。
    """
    pkg = (
        db.query(ContactPackage)
        .filter(
            ContactPackage.tenant_id == tenant_id,
            ContactPackage.account_id == account_id,
            ContactPackage.deleted_at.is_(None),
            ContactPackage.is_enabled.is_(True),
        )
        .order_by(ContactPackage.is_default.desc(), ContactPackage.updated_at.desc())
        .first()
    )
    return pkg


def _build_guide_content(intent_type: Optional[str]) -> str:
    """为评论自动回评生成稳定的话术。

    这里不追求花哨的 AI 生成，而是优先保证动作明确、可回收。
    用户看到的是“回关键词后自动发资料”，这比自由发挥更稳。
    """
    from app.services.wechat_lead_service import generate_reply_content

    return generate_reply_content(intent_type, keyword=AUTO_GUIDE_KEYWORD)


async def process_comment_lead_auto_conversion(
    db: Session,
    tenant_id: int,
    lead_id: int,
    operator_id: Optional[int] = None,
) -> dict:
    """把单条评论线索自动推进到“发资料”或“引导回评”。

    这个函数是整条自动化链路的入口，给同步任务和补漏任务复用。
    返回值统一包含 action、status、lead_id，方便前端与日志消费。
    """
    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == tenant_id,
    ).first()
    if not lead:
        return {"action": "skipped", "reason": "lead_not_found", "lead_id": lead_id}

    comment = db.query(WeChatComment).filter(
        WeChatComment.id == lead.comment_id,
    ).first()
    if not comment:
        return {"action": "skipped", "reason": "comment_not_found", "lead_id": lead_id}

    # 已经进入最终态的线索不重复推进，避免重复回评或重复发资料。
    if lead.status in ("contact_sent", "converted", "closed"):
        return {"action": "skipped", "reason": "lead_already_done", "lead_id": lead_id}

    from app.services.wechat_eligibility_service import (
        cache_eligibility,
        check_contact_eligibility,
    )

    eligibility = await check_contact_eligibility(
        db,
        tenant_id,
        lead.account_id,
        lead.openid,
        force_refresh=True,
    )
    cache_eligibility(db, lead.id, eligibility, tenant_id)

    package = _pick_enabled_package(db, tenant_id, lead.account_id)

    # 1. 可私信：直接创建发送任务并执行，尽量一步到位。
    if eligibility.eligible and package:
        from app.services.wechat_delivery_service import create_delivery, execute_delivery, get_delivery

        key = f"comment-auto:{lead.id}:{package.id}"
        delivery = create_delivery(
            db,
            tenant_id,
            lead.id,
            lead.account_id,
            lead.openid,
            package.id,
            operator_id or 0,
            key,
        )
        await execute_delivery(delivery.id)

        db.expire_all()
        refreshed = get_delivery(db, tenant_id, delivery.id)
        lead = db.query(CommentLead).filter(
            CommentLead.id == lead_id,
            CommentLead.tenant_id == tenant_id,
        ).first()
        if lead:
            lead.contact_package_id = package.id
            lead.last_action_at = datetime.now(timezone.utc)
            lead.status = "contact_sent" if refreshed and refreshed.get("status") == "success" else "manual_review"
            db.commit()

        return {
            "action": "sent_contact",
            "lead_id": lead_id,
            "delivery_id": delivery.id,
            "delivery_status": refreshed["status"] if refreshed else None,
            "package_id": package.id,
            "eligibility": eligibility.to_dict(),
        }

    # 2. 不可私信或未配置资料包：自动公开回评，引导用户回关键词。
    from app.services.wechat_lead_service import public_reply

    reply_content = _build_guide_content(lead.intent_type)
    reply_result = await public_reply(
        db,
        tenant_id,
        lead.id,
        "guide",
        reply_content,
        operator_id or 0,
        guide_keyword=AUTO_GUIDE_KEYWORD,
        auto_send_on_message=bool(package),
        auto_send_package_id=package.id if package else None,
    )

    if reply_result and package:
        lead.contact_package_id = package.id
        lead.last_action_at = datetime.now(timezone.utc)
        db.commit()

    return {
        "action": "guided_reply",
        "lead_id": lead_id,
        "reply_result": reply_result,
        "package_id": package.id if package else None,
        "eligibility": eligibility.to_dict(),
    }


async def process_comment_leads_auto_conversion(
    db: Session,
    tenant_id: int,
    lead_ids: list[int],
    operator_id: Optional[int] = None,
) -> list[dict]:
    """批量推进一组线索。

    同步任务往往一次会拿到多条新评论，批量入口能把“遍历+调度”的职责
    从 API 层收回到业务服务里，减少耦合。
    """
    results: list[dict] = []
    for lead_id in lead_ids:
        results.append(
            await process_comment_lead_auto_conversion(
                db,
                tenant_id,
                lead_id,
                operator_id=operator_id,
            )
        )
    return results
