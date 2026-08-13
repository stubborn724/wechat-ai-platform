"""文章投递结果的统一状态收敛服务。

发布器只负责与微信直连或中转站通信，返回值可能是草稿成功、发布已受理、最终成功
或明确失败。本模块将这些协议结果转换为 ``Article`` 与 ``PublishAttempt`` 的统一
事实，避免任务层根据初始文章状态推断投递成功。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class PublishDeliveryOutcome:
    """一次账号级投递的标准化结果，不依赖具体 ORM 类型。"""

    article_status: str
    article_phase: str
    attempt_status: str
    error_code: str | None = None
    error_message: str | None = None
    media_id: str | None = None
    publish_id: str | None = None
    message_id: str | None = None


def resolve_publish_delivery_outcome(publish_mode: str, result: dict[str, Any] | None) -> PublishDeliveryOutcome:
    """按发布模式解析发布器返回值，拒绝把不完整返回当作成功。

    ``direct`` 模式只有拿到发布任务 ID，或中转站明确返回粉丝群发成功，才可继续
    后续状态流程。仅保存草稿但发布失败时必须进入终态失败，避免永久 ``publishing``。
    """

    mode = str(publish_mode or "").strip().lower()
    payload = result or {}
    if mode == "draft":
        media_id = _string_or_none(payload.get("media_id"))
        if media_id:
            return PublishDeliveryOutcome(
                article_status="draft_saved",
                article_phase="DRAFT_SAVED",
                attempt_status="success",
                media_id=media_id,
            )
        return _failure_outcome("DRAFT_DELIVERY_FAILED", _result_message(payload, "微信未返回草稿标识"))

    if mode != "direct":
        return _failure_outcome("INVALID_PUBLISH_MODE", f"不支持的发布模式：{publish_mode}")

    media_id = _string_or_none(payload.get("media_id"))
    publish_id = _string_or_none(payload.get("publish_id"))
    message_id = _string_or_none(payload.get("msg_id"))
    publish_error = _string_or_none(payload.get("publish_error"))
    if publish_error:
        return _failure_outcome("PUBLISH_SUBMISSION_FAILED", publish_error, media_id=media_id)

    # 私域粉丝群发由中转站同步确认完成，不再等待 freepublish 轮询。
    if message_id:
        return PublishDeliveryOutcome(
            article_status="published",
            article_phase="PUBLISHED",
            attempt_status="success",
            media_id=media_id,
            message_id=message_id,
        )

    if publish_id:
        relay_status = _string_or_none(payload.get("relay_status"))
        return PublishDeliveryOutcome(
            article_status="publishing",
            article_phase="RELAY_PUBLISHING" if relay_status else "PUBLISHING",
            attempt_status="publishing",
            media_id=media_id,
            publish_id=publish_id,
        )

    return _failure_outcome(
        "PUBLISH_SUBMISSION_FAILED",
        _result_message(payload, "微信未返回 publishId"),
        media_id=media_id,
    )


def failure_publish_delivery_outcome(publish_mode: str, error_message: str) -> PublishDeliveryOutcome:
    """将发布器抛出的异常转换为可审计的终态失败。"""

    mode = str(publish_mode or "").strip().lower()
    code = "DRAFT_DELIVERY_FAILED" if mode == "draft" else "PUBLISH_SUBMISSION_FAILED"
    return _failure_outcome(code, error_message or "微信投递失败")


def apply_publish_delivery_outcome(article: Any, attempt: Any, outcome: PublishDeliveryOutcome,
                                   now: datetime | None = None) -> None:
    """把标准化结果写回文章与账号级投递记录。

    Article 表示对外可见文章事实，PublishAttempt 保留每个公众号的细粒度投递结果；
    两者必须在同一事务中更新，避免查询端读取到相互矛盾的成功状态。
    """

    recorded_at = now or datetime.now(timezone.utc)
    article.status = outcome.article_status
    article.phase = outcome.article_phase
    if outcome.media_id:
        article.publish_id = outcome.media_id
    if outcome.publish_id:
        article.publish_id = outcome.publish_id
    if outcome.message_id:
        article.msg_data_id = outcome.message_id

    attempt.status = outcome.attempt_status
    if outcome.media_id:
        attempt.platform_media_id = outcome.media_id
    if outcome.publish_id:
        attempt.platform_media_id = outcome.publish_id
    if outcome.message_id:
        attempt.platform_message_id = outcome.message_id

    if outcome.attempt_status == "publishing":
        article.wechat_publish_time = recorded_at
        attempt.started_at = recorded_at
        article.error_message = None
        attempt.error_code = None
        attempt.error_message = None
        return

    attempt.finished_at = recorded_at
    article.error_message = outcome.error_message
    attempt.error_code = outcome.error_code
    attempt.error_message = outcome.error_message


def expire_unresolved_relay_publish(article: Any, attempts: Iterable[Any], *, now: datetime,
                                    timeout_seconds: int) -> bool:
    """将没有状态查询能力的 relay 发布收敛为可观测失败。

    中转站当前只返回“已提交”而没有最终状态查询协议。为避免文章无限停在
    ``PUBLISHING``，仅对明确标记为 ``RELAY_PUBLISHING`` 的任务在超时后置失败；
    直连微信任务仍由原有 freepublish 轮询处理。
    """

    if getattr(article, "status", None) != "publishing":
        return False
    if getattr(article, "phase", None) != "RELAY_PUBLISHING":
        return False

    recorded_at = _submission_time(article)
    if recorded_at is not None and _elapsed_seconds(recorded_at, now) < max(int(timeout_seconds), 1):
        return False

    error_code = "PUBLISH_STATUS_UNAVAILABLE"
    error_message = "微信中转站已受理发布，但当前没有最终状态查询能力；请到公众号后台核验。"
    article.status = "failed"
    article.phase = error_code
    article.error_message = error_message
    for attempt in attempts:
        if getattr(attempt, "status", None) not in {"pending", "queued", "publishing", "retrying"}:
            continue
        attempt.status = "failed"
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.finished_at = now
    return True


def _failure_outcome(error_code: str, error_message: str, media_id: str | None = None) -> PublishDeliveryOutcome:
    """创建统一失败结果，减少任务层对错误码的重复拼接。"""

    phase = "DRAFT_DELIVERY_FAILED" if error_code == "DRAFT_DELIVERY_FAILED" else "PUBLISH_SUBMISSION_FAILED"
    if error_code == "INVALID_PUBLISH_MODE":
        phase = error_code
    return PublishDeliveryOutcome(
        article_status="failed",
        article_phase=phase,
        attempt_status="failed",
        error_code=error_code,
        error_message=error_message,
        media_id=media_id,
    )


def _result_message(result: dict[str, Any], fallback: str) -> str:
    """从受控响应中选择不会为空的诊断信息。"""

    return _string_or_none(result.get("message")) or fallback


def _string_or_none(value: Any) -> str | None:
    """把协议字段规范为非空字符串，避免 None 被写入状态判断。"""

    text = str(value or "").strip()
    return text or None


def _submission_time(article: Any) -> datetime | None:
    """选择发布提交时间；历史缺失时间戳的记录会在下一次轮询显式失败。"""

    return (
        getattr(article, "wechat_publish_time", None)
        or getattr(article, "updated_at", None)
        or getattr(article, "created_at", None)
    )


def _elapsed_seconds(started_at: datetime, now: datetime) -> float:
    """兼容 MySQL 无时区时间和测试中的带时区时间，计算安全的经过秒数。"""

    normalized_start = _to_utc(started_at)
    normalized_now = _to_utc(now)
    return (normalized_now - normalized_start).total_seconds()


def _to_utc(value: datetime) -> datetime:
    """将数据库时间统一为 UTC，避免有无时区对象相减抛出异常。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
