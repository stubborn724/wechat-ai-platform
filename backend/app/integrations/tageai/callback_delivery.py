"""TaGeAI Gateway 状态回调的可靠 outbox 投递器。

本模块只负责把已经持久化的状态事件投递到受控 Gateway 地址；它不重新计算文章状态、
不创建 ContentJob，也不保存签名密钥。状态快照和事件 ID 均来自数据库 outbox，因此 HTTP
超时、Worker 重启或 Gateway 暂时不可达只会触发同一事件的有限重试，不会丢失业务事实。
"""

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from sqlalchemy import or_

from app.database import MysqlSessionLocal
from app.integrations.tageai.auth import _build_signature, find_tageai_client_config_by_binding
from app.models.mysql_models import TageAiIntegrationCallbackOutbox, TageAiIntegrationInvocation

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 8
_DELIVERY_BATCH_SIZE = 50
_REQUEST_TIMEOUT_SECONDS = 15


def deliver_due_callback_events(limit: int = _DELIVERY_BATCH_SIZE) -> dict[str, int]:
    """投递到期 outbox 事件，并为可恢复失败计算退避时间。

    每条事件独立事务更新投递结果。这样批次中某一个 Gateway 配置错误或网络错误不会回滚
    其他租户已经完成的回调；达到最大重试次数的记录保留为 FAILED，供运维修复配置后人工补偿。
    """

    db = MysqlSessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = (
            db.query(TageAiIntegrationCallbackOutbox)
            .filter(
                TageAiIntegrationCallbackOutbox.status == "PENDING",
                or_(
                    TageAiIntegrationCallbackOutbox.next_attempt_at.is_(None),
                    TageAiIntegrationCallbackOutbox.next_attempt_at <= now,
                ),
            )
            .order_by(TageAiIntegrationCallbackOutbox.id.asc())
            .limit(max(1, min(limit, _DELIVERY_BATCH_SIZE)))
            .all()
        )
        delivered = 0
        failed = 0
        for row in rows:
            if _deliver_one(db, row):
                delivered += 1
            else:
                failed += 1
        return {"delivered": delivered, "failed": failed, "selected": len(rows)}
    finally:
        db.close()


def _deliver_one(db, outbox: TageAiIntegrationCallbackOutbox) -> bool:
    """签名投递一条 outbox 事件，并只记录脱敏诊断信息。"""

    invocation = db.query(TageAiIntegrationInvocation).filter(
        TageAiIntegrationInvocation.id == outbox.invocation_id,
        TageAiIntegrationInvocation.tenant_id == outbox.tenant_id,
    ).first()
    config = find_tageai_client_config_by_binding(invocation.tenant_binding_id) if invocation else None
    callback_url = str((config or {}).get("gateway_callback_url") or "").strip()
    client_id = str((config or {}).get("client_id") or "").strip()
    signing_secret = str((config or {}).get("signing_secret") or "").strip()
    if not callback_url or not client_id or not signing_secret:
        _record_delivery_failure(db, outbox, "Gateway 回调连接未配置", retryable=True)
        return False

    try:
        target = urlsplit(callback_url)
        if target.scheme not in {"http", "https"} or not target.netloc or not target.path:
            raise ValueError("gateway_callback_url 无效")
        canonical_query = urlencode(sorted(parse_qsl(target.query, keep_blank_values=True)), doseq=True, safe="~")
        body_text = json.dumps(outbox.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        timestamp = str(int(time.time()))
        # eventId 负责业务去重，nonce 负责 HTTP 签名防重放。重试必须生成新 nonce，否则
        # Gateway 的短期 replay 防护会把合法的同一事件重投误判为攻击。
        nonce = f"tageai-callback-{uuid.uuid4().hex}"
        signature = _build_signature(
            signing_secret=signing_secret,
            client_id=client_id,
            method="POST",
            canonical_path=target.path,
            canonical_query=canonical_query,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=body_text.encode("utf-8"),
        )
        response = httpx.post(
            callback_url,
            content=body_text.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-TageAI-Client-Id": client_id,
                "X-TageAI-Timestamp": timestamp,
                "X-TageAI-Nonce": nonce,
                "X-TageAI-Signature": f"sha256={signature}",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if 200 <= response.status_code < 300:
            outbox.status = "DELIVERED"
            outbox.delivered_at = datetime.now(timezone.utc)
            outbox.last_error = None
            db.commit()
            logger.info("TaGeAI callback delivered: event=%s invocation=%s", outbox.event_id, outbox.invocation_id)
            return True
        _record_delivery_failure(db, outbox, f"Gateway 回调返回 HTTP {response.status_code}", retryable=True)
        return False
    except Exception as exc:
        _record_delivery_failure(db, outbox, str(exc), retryable=True)
        logger.warning("TaGeAI callback delivery failed: event=%s error=%s", outbox.event_id, exc)
        return False


def _record_delivery_failure(
    db,
    outbox: TageAiIntegrationCallbackOutbox,
    message: str,
    *,
    retryable: bool,
) -> None:
    """记录有限指数退避；失败摘要截断，避免将远端页面或凭据写入数据库。"""

    outbox.attempt_count = int(outbox.attempt_count or 0) + 1
    outbox.last_error = str(message or "回调投递失败")[:500]
    if not retryable or outbox.attempt_count >= _MAX_ATTEMPTS:
        outbox.status = "FAILED"
        outbox.next_attempt_at = None
    else:
        backoff_seconds = min(300, 5 * (2 ** max(0, outbox.attempt_count - 1)))
        outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
    db.commit()
