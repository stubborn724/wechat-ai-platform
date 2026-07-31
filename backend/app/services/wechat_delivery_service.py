"""资料发送任务 — 创建、执行、查询、重试"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import (
    ContactDelivery, ContactDeliveryAttempt, ContactPackage,
    CommentLead, WeChatComment,
)
# 模块级引用（允许测试时 mock）
import app.services.wechat_message_service as _wms
import app.services.wechat_eligibility_service as _elig
import app.services.wechat_media_service as _media

logger = logging.getLogger(__name__)


def create_delivery(
    db: Session,
    tenant_id: int,
    lead_id: int,
    account_id: int,
    openid: str,
    package_id: int,
    operator_id: int,
    idempotency_key: str,
) -> ContactDelivery:
    """创建发送任务（幂等）"""
    # 幂等检查
    existing = db.query(ContactDelivery).filter(
        ContactDelivery.tenant_id == tenant_id,
        ContactDelivery.idempotency_key == idempotency_key,
    ).first()
    if existing:
        return existing

    # 检查资料包
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == package_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        raise ValueError("package deleted or not found")
    if not pkg.is_enabled:
        raise ValueError("package is disabled")
    if not pkg.qr_asset_id:
        raise ValueError("package missing qr_asset_id")

    # 保存快照
    package_snapshot = {
        "name": pkg.name,
        "contact_name": pkg.contact_name,
        "wechat_id": pkg.wechat_id,
        "phone": pkg.phone,
        "text_content": pkg.text_content,
        "qr_asset_id": pkg.qr_asset_id,
    }

    delivery = ContactDelivery(
        tenant_id=tenant_id,
        account_id=account_id,
        lead_id=lead_id,
        openid=openid,
        package_id=package_id,
        idempotency_key=idempotency_key,
        status="pending",
        delivery_mode=settings.wechat_send_mode,
        package_snapshot=package_snapshot,
        created_by=operator_id,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    # 更新资料包使用计数
    pkg.usage_count = (pkg.usage_count or 0) + 1
    db.commit()

    return delivery


def get_delivery(db: Session, tenant_id: int, delivery_id: int) -> Optional[dict]:
    """查询发送任务"""
    d = db.query(ContactDelivery).filter(
        ContactDelivery.id == delivery_id,
        ContactDelivery.tenant_id == tenant_id,
    ).first()
    if not d:
        return None
    return _delivery_to_dict(db, d)


def list_deliveries_by_lead(db: Session, tenant_id: int, lead_id: int) -> list[dict]:
    """线索的所有发送记录"""
    rows = db.query(ContactDelivery).filter(
        ContactDelivery.tenant_id == tenant_id,
        ContactDelivery.lead_id == lead_id,
    ).order_by(ContactDelivery.created_at.desc()).all()
    return [_delivery_to_dict(db, r) for r in rows]


def _delivery_to_dict(db: Session, d: ContactDelivery) -> dict:
    attempts_q = db.query(ContactDeliveryAttempt).filter(
        ContactDeliveryAttempt.delivery_id == d.id,
    ).order_by(ContactDeliveryAttempt.attempt_no.asc()).all()

    return {
        "id": d.id,
        "lead_id": d.lead_id,
        "account_id": d.account_id,
        "openid": d.openid,
        "package_id": d.package_id,
        "status": d.status,
        "delivery_mode": d.delivery_mode,
        "package_snapshot": d.package_snapshot,
        "eligibility_snapshot": d.eligibility_snapshot,
        "text_status": d.text_status,
        "text_attempts": d.text_attempts,
        "text_error_code": d.text_error_code,
        "text_error_message": d.text_error_message,
        "text_sent_at": d.text_sent_at.isoformat() if d.text_sent_at else None,
        "qr_status": d.qr_status,
        "qr_attempts": d.qr_attempts,
        "qr_error_code": d.qr_error_code,
        "qr_error_message": d.qr_error_message,
        "qr_sent_at": d.qr_sent_at.isoformat() if d.qr_sent_at else None,
        "attempts": [
            {
                "id": a.id,
                "step": a.step,
                "attempt_no": a.attempt_no,
                "status": a.status,
                "error_code": a.error_code,
                "error_message": a.error_message,
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            }
            for a in attempts_q
        ],
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "started_at": d.started_at.isoformat() if d.started_at else None,
        "completed_at": d.completed_at.isoformat() if d.completed_at else None,
    }


def _record_attempt(db: Session, delivery_id: int, step: str, attempt_no: int) -> ContactDeliveryAttempt:
    attempt = ContactDeliveryAttempt(
        delivery_id=delivery_id,
        step=step,
        attempt_no=attempt_no,
        status="processing",
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _complete_attempt(db: Session, attempt: ContactDeliveryAttempt,
                      status: str, error_code: str = None, error_message: str = None,
                      response_snapshot: dict = None):
    """完成单次发送尝试。

    这里使用 merge 是为了兼容前面为了控制会话生命周期而关闭过的 Session。
    """
    attempt = db.merge(attempt)
    attempt.status = status
    attempt.completed_at = datetime.now(timezone.utc)
    if error_code:
        attempt.error_code = error_code
    if error_message:
        attempt.error_message = error_message
    if response_snapshot:
        attempt.response_snapshot = response_snapshot
    db.commit()


def _ensure_send_result_ok(result: dict, step: str):
    """把微信客服消息的 errcode 显式转换成异常。

    `wechat_message_service` 会记录消息结果，但不会因为 errcode 非 0 自动抛错。
    发送编排层必须自己兜住这一点，否则 delivery 会被误判为成功。
    """
    if not isinstance(result, dict):
        return
    errcode = result.get("errcode", 0)
    if errcode != 0:
        errmsg = result.get("errmsg", "unknown error")
        raise RuntimeError(f"{step} send failed: errcode={errcode}, errmsg={errmsg}")


# ---- 异步执行核心 ----

async def execute_delivery(delivery_id: int):
    """异步执行发送任务

    流程: 资格检查 → 准备素材 → 发送文字 → 发送二维码
    短事务设计: 微信 API 调用期间不持有 Session
    """
    from app.database import MysqlSessionLocal

    db = MysqlSessionLocal()
    try:
        delivery = db.query(ContactDelivery).filter(
            ContactDelivery.id == delivery_id,
        ).first()
        if not delivery:
            raise ValueError(f"Delivery {delivery_id} not found")
        if delivery.status != "pending":
            logger.info("Delivery %s already started (%s), skipping", delivery_id, delivery.status)
            return

        now = datetime.now(timezone.utc)

        # --- 1. 资格检查 ---
        _update_status(db, delivery, "checking_eligibility")
        db.close()
        db = MysqlSessionLocal()

        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()
        try:
            from app.models.mysql_models import CommentLead
            lead = db.query(CommentLead).filter(CommentLead.id == delivery.lead_id).first()
            openid = lead.openid if lead else delivery.openid

            eligibility = await _elig.check_contact_eligibility(
                db, delivery.tenant_id, delivery.account_id,
                openid or delivery.openid, force_refresh=True,
            )
            delivery.eligibility_snapshot = eligibility.to_dict()
            delivery.started_at = now
            db.commit()

            if not eligibility.eligible:
                delivery.status = "ineligible"
                db.commit()
                logger.info("Delivery %d ineligible: %s", delivery_id, eligibility.reason_code)
                return
        except Exception as e:
            delivery.status = "failed"
            delivery.text_error_message = f"资格检查失败: {e}"
            db.commit()
            raise

        # --- 2. 准备二维码素材 ---
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        _update_status(db, delivery, "preparing_media")
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        try:
            snapshot = delivery.package_snapshot or {}
            qr_asset_id = snapshot.get("qr_asset_id")
            if qr_asset_id:
                media_asset = await _media.get_or_prepare_image_media(
                    db, delivery.tenant_id, delivery.account_id, int(qr_asset_id),
                )
                media_id = media_asset.media_id
                if not media_asset.is_mock:
                    delivery.qr_error_code = media_asset.last_error_code
                    delivery.qr_error_message = media_asset.last_error_message
                db.commit()
            else:
                delivery.qr_status = "failed"
                delivery.qr_error_message = "资料包快照中无二维码配置"
                delivery.status = "failed"
                db.commit()
                return
        except Exception as e:
            delivery.qr_status = "failed"
            delivery.qr_error_message = f"素材准备失败: {e}"
            delivery.status = "partial_failed"
            db.commit()
            raise

        # --- 3. 发送文字 ---
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        _update_status(db, delivery, "sending_text")
        attempt_text = _record_attempt(db, delivery.id, "text", delivery.text_attempts + 1)
        delivery.text_attempts += 1
        db.commit()
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        text_sent = False
        try:
            text_content = (delivery.package_snapshot or {}).get("text_content", "")
            text_result = await _wms.send_text_message(
                db, delivery.tenant_id, delivery.account_id,
                delivery.openid, text_content or "",
            )
            _ensure_send_result_ok(text_result, "text")

            delivery.text_status = "success"
            delivery.text_sent_at = datetime.now(timezone.utc)
            delivery.text_error_code = None
            delivery.text_error_message = None
            _complete_attempt(db, attempt_text, "success")
            text_sent = True
        except Exception as e:
            delivery.text_status = "failed"
            delivery.text_error_message = str(e)
            _complete_attempt(db, attempt_text, "failed", error_message=str(e))

        # --- 4. 发送二维码 ---
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        _update_status(db, delivery, "sending_qr")
        attempt_qr = _record_attempt(db, delivery.id, "qr", delivery.qr_attempts + 1)
        delivery.qr_attempts += 1
        db.commit()
        db.close()
        db = MysqlSessionLocal()
        delivery = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        qr_sent = False
        try:
            if not text_sent:
                raise RuntimeError("text send failed, skip qr")

            image_result = await _wms.send_image_message(
                db, delivery.tenant_id, delivery.account_id,
                delivery.openid, media_id or "",
            )
            _ensure_send_result_ok(image_result, "qr")

            delivery.qr_status = "success"
            delivery.qr_sent_at = datetime.now(timezone.utc)
            delivery.qr_error_code = None
            delivery.qr_error_message = None
            _complete_attempt(db, attempt_qr, "success")
            qr_sent = True
        except Exception as e:
            delivery.qr_status = "failed"
            delivery.qr_error_message = str(e)
            _complete_attempt(db, attempt_qr, "failed", error_message=str(e))

        # --- 5. 计算最终状态 ---
        delivery.status = _compute_final_status(text_sent, qr_sent)
        delivery.completed_at = datetime.now(timezone.utc)

        # 更新 lead 状态
        if delivery.lead_id:
            from app.models.mysql_models import CommentLead
            lead = db.query(CommentLead).filter(CommentLead.id == delivery.lead_id).first()
            if lead and delivery.status in ("success", "partial_failed"):
                lead.status = "contact_sent"
            lead.last_action_at = datetime.now(timezone.utc)

        db.commit()
        logger.info("Delivery %d completed with status=%s", delivery_id, delivery.status)

    except Exception as exc:
        try:
            d2 = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()
            if d2 and d2.status not in ("success", "partial_failed", "ineligible"):
                d2.status = "failed"
                if not d2.text_error_message:
                    d2.text_error_message = str(exc)
                db.commit()
        except Exception:
            pass
        logger.error("Delivery %d failed: %s", delivery_id, exc)
        raise
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _update_status(db: Session, delivery: ContactDelivery, status: str):
    delivery.status = status
    db.commit()


def _compute_final_status(text_sent: bool, qr_sent: bool) -> str:
    if text_sent and qr_sent:
        return "success"
    if text_sent and not qr_sent:
        return "partial_failed"
    return "failed"


# ---- 重试 ----

async def retry_delivery(
    db: Session,
    tenant_id: int,
    delivery_id: int,
    step: str,
    idempotency_key: str,
    operator_id: int = None,
) -> ContactDelivery:
    """重试指定步骤"""
    delivery = db.query(ContactDelivery).filter(
        ContactDelivery.id == delivery_id,
        ContactDelivery.tenant_id == tenant_id,
    ).first()

    if not delivery:
        raise ValueError(f"Delivery {delivery_id} not found")

    if delivery.status in ("sending_text", "sending_qr", "preparing_media", "checking_eligibility"):
        raise ValueError(f"Delivery 当前状态为 {delivery.status}，无法重试")

    if delivery.status in ("success", "ineligible"):
        raise ValueError(f"Delivery 状态为 {delivery.status}，不允许重试")

    # 幂等
    existing = db.query(ContactDelivery).filter(
        ContactDelivery.tenant_id == tenant_id,
        ContactDelivery.idempotency_key == idempotency_key,
    ).first()
    if existing and existing.id != delivery_id:
        raise ValueError("幂等键冲突")

    # 文本已成功不能重试 text
    if step == "text" and delivery.text_status == "success":
        raise ValueError("文本步骤已成功，不允许重试")

    # 最大次数
    max_attempts = 3

    # 重试前重新检查资格
    eligibility = await _elig.check_contact_eligibility(
        db, tenant_id, delivery.account_id, delivery.openid, force_refresh=True,
    )
    delivery.eligibility_snapshot = eligibility.to_dict()
    if not eligibility.eligible:
        delivery.status = "ineligible"
        db.commit()
        raise ValueError(f"资格检查未通过: {eligibility.reason_text}")

    # 执行重试
    db.commit()

    from app.database import MysqlSessionLocal
    
    db2 = MysqlSessionLocal()
    try:
        d2 = db2.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

        if step in ("text", "all") and d2.text_status != "success":
            if d2.text_attempts >= max_attempts:
                raise ValueError(f"文本步骤已达最大重试次数 {max_attempts}")

            attempt = _record_attempt(db2, delivery_id, "text", d2.text_attempts + 1)
            d2.text_attempts += 1
            db2.commit()
            db2.close()
            db2 = MysqlSessionLocal()
            d2 = db2.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

            try:
                text_content = (d2.package_snapshot or {}).get("text_content", "")
                text_result = await _wms.send_text_message(db2, tenant_id, d2.account_id, d2.openid, text_content or "")
                _ensure_send_result_ok(text_result, "text")
                d2.text_status = "success"
                d2.text_error_code = None
                d2.text_error_message = None
                d2.text_sent_at = datetime.now(timezone.utc)
                _complete_attempt(db2, attempt, "success")
            except Exception as e:
                d2.text_status = "failed"
                d2.text_error_message = str(e)
                _complete_attempt(db2, attempt, "failed", error_message=str(e))

        if step in ("qr", "all") and d2.qr_status != "success":
            if d2.qr_attempts >= max_attempts:
                raise ValueError(f"二维码步骤已达最大重试次数 {max_attempts}")

            attempt_qr = _record_attempt(db2, delivery_id, "qr", d2.qr_attempts + 1)
            d2.qr_attempts += 1
            db2.commit()
            db2.close()
            db2 = MysqlSessionLocal()
            d2 = db2.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()

            try:
                # 先准备素材
                snapshot = d2.package_snapshot or {}
                qr_asset_id = snapshot.get("qr_asset_id")
                if qr_asset_id:
                    media_asset = await _media.get_or_prepare_image_media(
                        db2, tenant_id, d2.account_id, int(qr_asset_id), force_refresh=True,
                    )
                    media_id = media_asset.media_id
                else:
                    media_id = None

                if True and media_id:
                    image_result = await _wms.send_image_message(db2, tenant_id, d2.account_id, d2.openid, media_id)
                    _ensure_send_result_ok(image_result, "qr")

                d2.qr_status = "success"
                d2.qr_error_code = None
                d2.qr_error_message = None
                d2.qr_sent_at = datetime.now(timezone.utc)
                _complete_attempt(db2, attempt_qr, "success")
            except Exception as e:
                d2.qr_status = "failed"
                d2.qr_error_message = str(e)
                _complete_attempt(db2, attempt_qr, "failed", error_message=str(e))

        # 重新计算总状态
        ts = d2.text_status == "success"
        qs = d2.qr_status == "success"
        d2.status = _compute_final_status(ts, qs)
        d2.completed_at = datetime.now(timezone.utc)
        db2.commit()

    finally:
        try:
            db2.rollback()
        except Exception:
            pass
        db2.close()

    db.refresh(delivery)
    return delivery
