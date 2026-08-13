"""TaGeAI Integration 持久化业务服务。

本模块负责把 Gateway 的服务间调用转换为平台既有的 ContentJob 队列任务。它不自己
生成文章、不启动模拟线程，也不伪造草稿或发布成功；查询结果始终由 ContentJob 和
Article 的真实状态聚合而来，因此重启后仍可继续查询、取消和对账。
"""

import hashlib
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from sqlalchemy.exc import IntegrityError

from app.database import MysqlSessionLocal
from app.models.mysql_models import (
    Article,
    ContentJob,
    ContentVersion,
    PublishAttempt,
    TageAiPublishCandidate,
    TageAiIntegrationCallbackEvent,
    TageAiIntegrationCallbackOutbox,
    TageAiIntegrationInvocation,
    WeChatAccount,
)
from app.services.tageai_generation_budget_service import (
    BudgetApprovalRequired,
    GenerationBudgetError,
    normalize_generation_budget,
)
from app.services.tageai_platform_progress_service import project_platform_progress
from app.integrations.tageai.publish_candidate import PublishCandidateError, claim_publish_candidate

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {"DRAFT_SAVED", "PUBLISHED", "FAILED", "CANCELLED"}
_PREVIEW_CANDIDATE_TTL = timedelta(hours=1)
# 尚未被 Worker 执行的任务可以立即取消；运行中的任务必须先进入取消请求中，等待
# Worker 在可协作停止的边界确认。把两类状态分开能避免“远端仍在发布、本地已取消”的
# 假终态。
_IMMEDIATELY_CANCELLABLE_JOB_STATUSES = {
    "pending", "queued", "dispatching", "awaiting_review", "approved", "scheduled",
}
_CANCEL_REQUESTABLE_JOB_STATUSES = {"generating", "cancel_requested"}


class IntegrationInputError(ValueError):
    """调用参数、租户账号绑定或业务前置条件不满足时抛出的可读错误。

    ``details`` 只承载经过服务端白名单整理的可恢复交互数据，例如生成预算超出默认值
    时的 requested/default/hard_limit；普通错误保持空值，避免把内部异常对象回传给桌面端。
    """

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details


class IntegrationIdempotencyConflictError(ValueError):
    """同一租户幂等键对应了不同业务载荷时抛出。

    该异常单独建模而不是复用参数错误，是因为调用方应停止使用当前幂等键并人工确认请求
    是否被错误复用；任何自动重试都不应覆盖已经创建的生成或发布任务。
    """


def create_invocation(
    invocation_id: str,
    tenant_id: int,
    operation: str,
    delivery_mode: str,
    target_account_ref: Optional[str],
    input_data: dict,
    execution_id: str,
    *,
    owner_user_id: Optional[str] = None,
    tenant_binding_id: Optional[str] = None,
    target_account_bindings: Optional[Mapping[str, object]] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """创建或幂等返回一个持久化的外部调用。

    Gateway 已经完成权限和确认准入；本平台仍必须自行校验租户和公众号账号绑定，
    并将调用持久化到与 ContentJob 同一事务中，避免“返回已接受但任务未入队”的双写缺口。
    """
    _validate_create_input(operation, delivery_mode, input_data)
    normalized_binding_id = str(tenant_binding_id or "").strip()
    normalized_idempotency_key = str(idempotency_key or "").strip()
    if not normalized_idempotency_key:
        raise IntegrationInputError("MISSING_IDEMPOTENCY_KEY", "缺少 Idempotency-Key")
    request_hash = _build_request_hash(
        tenant_binding_id=normalized_binding_id,
        operation=operation,
        delivery_mode=delivery_mode,
        target_account_ref=target_account_ref,
        input_data=input_data,
        execution_id=execution_id,
    )
    # 旧部署仍可通过非空静态映射在打开会话前拒绝未知账号。新连接器部署不配置映射，
    # 改为在当前租户数据库内按不可猜测的逻辑引用解析，绝不回退选择默认公众号。
    has_legacy_static_bindings = isinstance(target_account_bindings, Mapping) and bool(target_account_bindings)
    account_id = None
    if has_legacy_static_bindings:
        account_id = resolve_target_account_binding(
            tenant_binding_id=tenant_binding_id,
            target_account_ref=target_account_ref,
            target_account_bindings=target_account_bindings,
        )
    db = MysqlSessionLocal()
    try:
        if account_id is None:
            account_id = resolve_target_account_binding(
                tenant_binding_id=tenant_binding_id,
                target_account_ref=target_account_ref,
                target_account_bindings=None,
                connector_account_lookup=lambda account_ref: lookup_connector_account_id(
                    db, tenant_id=tenant_id, owner_user_id=owner_user_id, account_ref=account_ref,
                ),
            )
        # 幂等键是租户内的业务键，而不是 invocationId 的别名。先按租户键查询后比较摘要，
        # 可以阻止调用方复用同一 key 改写主题、目标账号或 DRAFT/PUBLISH 投递方式。
        existing = db.query(TageAiIntegrationInvocation).filter(
            TageAiIntegrationInvocation.tenant_id == tenant_id,
            TageAiIntegrationInvocation.idempotency_key == normalized_idempotency_key,
        ).first()
        if existing:
            _require_matching_idempotency_payload(existing, request_hash)
            return _serialize_invocation(existing, db)

        existing = db.query(TageAiIntegrationInvocation).filter(
            TageAiIntegrationInvocation.invocation_id == invocation_id,
        ).first()
        if existing:
            if existing.tenant_id != tenant_id:
                raise IntegrationInputError("PERMISSION_DENIED", "调用不属于当前租户")
            _require_matching_idempotency_payload(existing, request_hash)
            return _serialize_invocation(existing, db)

        account = _resolve_account(db, tenant_id, account_id)
        preview_candidate = None
        if delivery_mode == "PUBLISH":
            preview_candidate = _claim_preview_candidate(
                db,
                candidate_id=input_data.get("publish_candidate_id"),
                tenant_id=tenant_id,
                target_account_ref=str(target_account_ref),
                account_id=account.id,
                publish_invocation_id=invocation_id,
            )
        topic = (input_data.get("topic") or "公众号文章仿写").strip()
        if preview_candidate is not None:
            # 正式发布的展示主题应来自用户已经预览的版本，不再使用本轮请求携带的主题，
            # 防止调用方在确认后替换内容却复用同一候选。
            source_article = db.query(Article).filter(
                Article.id == preview_candidate.article_id,
                Article.tenant_id == tenant_id,
            ).first()
            if source_article is None:
                raise IntegrationInputError("PUBLISH_CANDIDATE_CONTENT_MISSING", "发布候选关联的文章不存在")
            topic = (source_article.main_title or source_article.topic or topic).strip()
        # ContentJob 使用独立的幂等键空间，避免与普通桌面端调用冲突。
        job_idempotency_key = f"tageai:{invocation_id}"[:128]
        job = ContentJob(
            tenant_id=tenant_id,
            account_id=account.id,
            status="queued",
            version=1,
            topic=topic[:255],
            # 预览任务在生成后停在等待确认的安全边界；发布任务则由专门的 Worker 分支
            # 对已冻结版本投递，绝不再次调用模型生成正文。
            content_type="article_publish_existing" if preview_candidate is not None else "article",
            approval_mode="manual" if delivery_mode == "PREVIEW" else "auto",
            idempotency_key=job_idempotency_key,
            generation_config=_build_generation_config(
                operation,
                delivery_mode,
                input_data,
                preview_candidate=preview_candidate,
            ),
        )
        db.add(job)
        db.flush()

        now = datetime.now(timezone.utc)
        invocation = TageAiIntegrationInvocation(
            invocation_id=invocation_id,
            tenant_id=tenant_id,
            tenant_binding_id=normalized_binding_id,
            content_job_id=job.id,
            operation=operation,
            delivery_mode=delivery_mode,
            # 对外逻辑引用可用于审计和结果展示；本地账号 ID 只保存在 ContentJob 的
            # 内部关联，不反向泄露给 TaGeAI 调用方。
            target_account_ref=str(target_account_ref),
            execution_id=execution_id,
            input_data=input_data,
            idempotency_key=normalized_idempotency_key,
            request_hash=request_hash,
            external_job_id=f"content-job-{job.id}",
            status="QUEUED",
            phase="QUEUED",
            progress=0,
            callback_event_ids=[],
            started_at=now,
        )
        db.add(invocation)
        db.flush()
        # 调用记录与首个 QUEUED 状态事件必须同事务落库。Gateway 即使在 202 响应后短暂
        # 不可达，也能由 outbox 重试获得“已受理”的真实状态，而不是依赖 HTTP 内存回调。
        _enqueue_callback_snapshot(db, invocation, _serialize_invocation(invocation, db))
        db.commit()
        db.refresh(invocation)
        logger.info("TaGeAI invocation created: id=%s job=%s tenant=%s", invocation_id, job.id, tenant_id)
        return _serialize_invocation(invocation, db)
    except IntegrityError:
        # 并发重试时由租户幂等键或 invocation_id 的唯一约束收敛到同一条调用记录；再次比对
        # 请求摘要，不能把数据库冲突误当作可安全复用。
        db.rollback()
        existing = db.query(TageAiIntegrationInvocation).filter(
            TageAiIntegrationInvocation.tenant_id == tenant_id,
            TageAiIntegrationInvocation.idempotency_key == normalized_idempotency_key,
        ).first()
        if existing is None:
            existing = db.query(TageAiIntegrationInvocation).filter(
                TageAiIntegrationInvocation.invocation_id == invocation_id,
            ).first()
        if existing:
            if existing.tenant_id != tenant_id:
                raise IntegrationInputError("PERMISSION_DENIED", "调用不属于当前租户")
            _require_matching_idempotency_payload(existing, request_hash)
            return _serialize_invocation(existing, db)
        raise
    finally:
        db.close()


def _build_request_hash(
    *,
    tenant_binding_id: str,
    operation: str,
    delivery_mode: str,
    target_account_ref: Optional[str],
    input_data: dict,
    execution_id: str,
) -> str:
    """生成跨重试稳定的请求摘要。

    摘要覆盖会改变外部副作用的全部字段，并使用确定性 JSON 编码。它不保存正文以外的敏感
    凭据，也不依赖 Python 字典插入顺序，因此 Gateway 重试、HTTP 字段重排或进程重启不会
    让同一业务请求获得不同指纹。
    """

    payload = {
        "tenantBindingId": tenant_binding_id,
        "operation": str(operation or "").strip(),
        "deliveryMode": str(delivery_mode or "").strip(),
        "targetAccountRef": str(target_account_ref or "").strip(),
        "input": input_data,
        "executionId": str(execution_id or "").strip(),
    }
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _require_matching_idempotency_payload(
    existing: TageAiIntegrationInvocation,
    request_hash: str,
) -> None:
    """确认重复请求与首个已接受请求完全一致，否则拒绝复用幂等键。"""

    if existing.request_hash != request_hash:
        raise IntegrationIdempotencyConflictError("同一 Idempotency-Key 已用于不同的文章请求")


def get_invocation(invocation_id: str, tenant_id: int) -> Optional[dict]:
    """按租户查询调用，并从真实内容任务聚合最新状态。

    relay 当前只承诺“发布已受理”，没有最终状态查询协议。Celery 轮询会主动做
    超时补偿，但外部 Gateway 的查询不能依赖 Beat 必须准点运行；因此这里会在读取
    到超时 relay 发布时同步持久化失败投影，确保调用方不会无限收到 ``PUBLISHING``。
    """
    db = MysqlSessionLocal()
    try:
        invocation = _find_invocation(db, invocation_id, tenant_id)
        if invocation is None:
            return None

        context = _load_invocation_delivery_context(invocation, db)
        # 超时收敛和 callback outbox 必须共用一次事务。若先提交 Invocation，再单独提交
        # outbox，进程会在两个提交点之间留下“状态已失败但回调未入队”的可见窗口，也会
        # 让一次查询产生两次数据库提交。先完成全部内存变更，最后统一提交即可。
        state_converged = _converge_stale_generation(invocation, context)
        state_converged = _converge_expired_relay_publish(invocation, context) or state_converged
        snapshot = _serialize_invocation(invocation, db, context)
        callback_created = _enqueue_callback_snapshot(db, invocation, snapshot)
        if state_converged or callback_created:
            db.commit()
        return snapshot
    finally:
        db.close()


def cancel_invocation(invocation_id: str, tenant_id: int) -> Optional[dict]:
    """请求取消外部调用，并只在没有运行中副作用时确认终态。

    ``generating`` 覆盖模型生成、素材处理和公众号投递前后的完整 Worker 生命周期。
    这类任务不能在 HTTP 请求中直接标记为 ``CANCELLED``：Worker 可能刚好等待外部
    服务返回。服务仅持久化 ``cancel_requested``，由 Worker 在每个阶段边界观察后把
    ContentJob 收敛为 ``cancelled``；查询投影随之成为真正的 ``CANCELLED``。
    """
    db = MysqlSessionLocal()
    try:
        invocation = _find_invocation(db, invocation_id, tenant_id)
        if invocation is None:
            return None
        current = _serialize_invocation(invocation, db)
        if current["status"] in _TERMINAL_STATES:
            return current

        job = db.query(ContentJob).filter(
            ContentJob.id == invocation.content_job_id,
            ContentJob.tenant_id == tenant_id,
        ).first()
        if job and job.status in _IMMEDIATELY_CANCELLABLE_JOB_STATUSES:
            job.status = "cancelled"
            invocation.status = "CANCELLED"
            invocation.phase = "CANCELLED"
            invocation.progress = 100
            invocation.finished_at = datetime.now(timezone.utc)
        elif job and job.status in _CANCEL_REQUESTABLE_JOB_STATUSES:
            # 重复点击取消保持幂等，不刷新完成时间或进度，直到 Worker 真正停止。
            job.status = "cancel_requested"
            invocation.status = "CANCEL_REQUESTED"
            invocation.phase = "CANCEL_REQUESTED"
            invocation.finished_at = None
        else:
            # 已进入不可撤销的外部投递阶段时保留真实状态给 Gateway 查询和对账；绝不
            # 以本地“取消成功”掩盖仍可能发生的公众号副作用。
            return current
        snapshot = _serialize_invocation(invocation, db)
        _enqueue_callback_snapshot(db, invocation, snapshot)
        db.commit()
        return snapshot
    finally:
        db.close()


def process_callback(
    invocation_id: str,
    tenant_id: int,
    external_job_id: str,
    status: str,
    phase: str,
    progress: int,
    result: Optional[dict],
    error: Optional[dict],
    event_id: str,
) -> bool:
    """持久化经过认证的回调，并以独立事件表在数据库层幂等去重。

    当前主流程由平台自身的 ContentJob 状态驱动，此入口保留给未来独立发布器回调。
    回调不能覆盖其他租户或其他内部任务的 Invocation；JSON 数组仅保留历史兼容，不再承担
    去重事实，避免高频事件被截断后重新产生副作用。
    """
    db = MysqlSessionLocal()
    try:
        invocation = _find_invocation(db, invocation_id, tenant_id)
        if invocation is None or invocation.external_job_id != external_job_id:
            return False
        event = TageAiIntegrationCallbackEvent(
            tenant_id=tenant_id,
            invocation_id=invocation.id,
            event_id=event_id,
            payload_hash=_build_callback_payload_hash(
                external_job_id=external_job_id,
                status=status,
                phase=phase,
                progress=progress,
                result=result,
                error=error,
            ),
        )
        db.add(event)
        try:
            # 唯一约束是跨进程、跨重启的最终去重边界。先 flush 再更新 Invocation，重复
            # event 不会覆盖更新后的新状态，也不会多次触发后续终态处理。
            db.flush()
        except IntegrityError:
            db.rollback()
            return True
        invocation.status = status
        invocation.phase = phase
        invocation.progress = max(0, min(progress, 100))
        invocation.result_data = result
        if error:
            invocation.error_code = error.get("error_code") or error.get("errorCode")
            invocation.error_message = error.get("message")
            invocation.retryable = bool(error.get("retryable", False))
        if status in _TERMINAL_STATES:
            invocation.finished_at = datetime.now(timezone.utc)
        _enqueue_callback_snapshot(db, invocation, _serialize_invocation(invocation, db))
        db.commit()
        return True
    finally:
        db.close()


def _build_callback_payload_hash(
    *,
    external_job_id: str,
    status: str,
    phase: str,
    progress: int,
    result: Optional[dict],
    error: Optional[dict],
) -> str:
    """为回调事件保存可审计的规范化摘要，不复制文章正文或平台凭据。"""

    payload = {
        "externalJobId": external_job_id,
        "status": status,
        "phase": phase,
        "progress": max(0, min(progress, 100)),
        "result": result,
        "error": error,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def list_pending_callbacks(since_seconds: int = 300) -> list[dict]:
    """返回未终态的持久化调用，供补偿工作器按需查询真实任务状态。"""
    db = MysqlSessionLocal()
    try:
        rows = db.query(TageAiIntegrationInvocation).filter(
            TageAiIntegrationInvocation.status.notin_(_TERMINAL_STATES),
        ).all()
        return [_serialize_invocation(row, db) for row in rows]
    finally:
        db.close()


def enqueue_current_callback_snapshots(limit: int = 200) -> int:
    """扫描真实任务状态并为变化的 Invocation 写入 outbox。

    Worker 可能直接更新 ContentJob、Article 或 PublishAttempt，不会经过 Integration Service。
    因而回调不能依赖某个 HTTP 路由被访问；该函数由 Beat 定期运行，从数据库事实重新计算
    状态，只为此前未投递过的快照创建 outbox 行。
    """

    db = MysqlSessionLocal()
    try:
        invocations = (
            db.query(TageAiIntegrationInvocation)
            .order_by(TageAiIntegrationInvocation.updated_at.asc())
            .limit(max(1, limit))
            .all()
        )
        created = 0
        needs_commit = False
        for invocation in invocations:
            context = _load_invocation_delivery_context(invocation, db)
            state_converged = _converge_stale_generation(invocation, context)
            snapshot = _serialize_invocation(invocation, db, context)
            callback_created = _enqueue_callback_snapshot(db, invocation, snapshot)
            if callback_created:
                created += 1
            # 状态收敛和回调快照必须在同一个事务中提交，保证重启恢复后既有明确失败，
            # 又不会出现“数据库已失败但 Gateway 永远收不到失败事件”的中间状态。
            needs_commit = needs_commit or state_converged or callback_created
        if needs_commit:
            db.commit()
        return created
    finally:
        db.close()


def derive_invocation_state(
    job: ContentJob,
    article: Optional[Article],
    delivery_mode: str,
    delivery_attempt: Optional[PublishAttempt] = None,
) -> dict:
    """将真实任务和文章事实映射为 TaGeAI 统一状态。

    不把 ``approved`` 直接解释成发布成功：只有文章草稿确实保存、或微信发布轮询确认
    ``published`` 后，才返回对应终态。这一映射同时被实时查询与补偿轮询复用。
    """
    if job.status == "cancelled":
        return _state("CANCELLED", "CANCELLED", 100)
    if job.status == "cancel_requested":
        return _state("CANCEL_REQUESTED", "CANCEL_REQUESTED", 50)
    if job.status == "failed":
        return _state("FAILED", "FAILED", 100, error_code=job.error_code or "GENERATION_FAILED",
                      error_message=job.error_message or "内容生成失败")
    # 正文和媒体仍由同一个 ContentJob 承载，但每次阶段提交都会把公开、脱敏的
    # 进度快照写入配置。Invocation 从该持久化事实计算平台进度，桌面端重启或回调
    # 丢失后通过轮询仍能得到同一结果，而不是退化成固定的 30%。
    progress_snapshot = (getattr(job, "generation_config", None) or {}).get("progress_snapshot")
    if job.status == "generating" and isinstance(progress_snapshot, dict):
        platform_progress = project_platform_progress(progress_snapshot)
        return _state(
            platform_progress["status"],
            platform_progress["stage"],
            platform_progress["progress"],
            platform=platform_progress["platform"],
            platform_label=platform_progress["platformLabel"],
            media_summary=platform_progress["mediaSummary"],
            estimated_remaining_seconds=platform_progress["estimatedRemainingSeconds"],
            heartbeat_at=progress_snapshot.get("heartbeat_at"),
            error_code=(platform_progress.get("error") or {}).get("code"),
            error_message=(platform_progress.get("error") or {}).get("message"),
            retryable=platform_progress["status"] == "FAILED",
        )
    if delivery_attempt and delivery_attempt.status == "failed":
        result = _article_result(article, delivery_mode) if article else None
        return _state(
            "FAILED",
            delivery_attempt.error_code or "DELIVERY_FAILED",
            100,
            result=result,
            error_code=delivery_attempt.error_code or "DELIVERY_FAILED",
            error_message=delivery_attempt.error_message or "文章投递失败",
        )
    if article:
        result = _article_result(article, delivery_mode)
        if article.status == "failed":
            # PublishAttempt 缺失时仍可能存在文章级失败事实，例如 relay 状态查询超时。
            # 必须保留该诊断码，不能把所有文章失败都错误归类为草稿投递失败。
            failure_code = _article_failure_code(article, delivery_mode)
            return _state("FAILED", failure_code, 100, result=result, error_code=failure_code,
                          error_message=article.error_message or "文章投递失败")
        if delivery_mode == "DRAFT" and article.status == "draft_saved":
            if delivery_attempt and delivery_attempt.status == "success":
                return _state("DRAFT_SAVED", "DRAFT_SAVED", 100, result=result)
            return _state("DELIVERING", "DRAFT_DELIVERY_PENDING", 90, result=result)
        if delivery_mode == "PUBLISH":
            if article.status == "published":
                return _state("PUBLISHED", "PUBLISHED", 100, result=result)
            if article.status == "publishing":
                return _state("PUBLISHING", "PUBLISHING", 90, result=result)
        if delivery_mode == "PREVIEW" and article.status == "generated" \
                and job.status in {"approved", "awaiting_review"}:
            # PREVIEW 的成功标准是可读取的同一版本文章，不是草稿或发布副作用。
            return _state("CONTENT_READY", "CONTENT_READY", 100, result=result)
        if job.status in {"approved", "awaiting_review"}:
            return _state("CONTENT_READY", "CONTENT_READY", 80, result=result)
    if job.status == "generating":
        return _state("GENERATING", "GENERATING", 30)
    if job.status in {"queued", "pending"}:
        return _state("QUEUED", "QUEUED", 0)
    return _state("CONTENT_READY", "CONTENT_READY", 80)


def _find_invocation(db, invocation_id: str, tenant_id: int) -> Optional[TageAiIntegrationInvocation]:
    return db.query(TageAiIntegrationInvocation).filter(
        TageAiIntegrationInvocation.invocation_id == invocation_id,
        TageAiIntegrationInvocation.tenant_id == tenant_id,
    ).first()


def _load_invocation_delivery_context(
    invocation: TageAiIntegrationInvocation,
    db,
) -> tuple[Optional[ContentJob], Optional[Article], list[PublishAttempt]]:
    """读取 Integration 状态投影所需的同租户交付事实。

    ``Article`` 通过最新 ``ContentVersion`` 反查，所有 ``PublishAttempt`` 则保留
    在同一上下文中：前者决定对外文章状态，后者既能覆盖陈旧文章字段，也能让超时
    收敛同步结束所有尚未终态的账号级投递记录。
    """

    job = db.query(ContentJob).filter(
        ContentJob.id == invocation.content_job_id,
        ContentJob.tenant_id == invocation.tenant_id,
    ).first()
    article = None
    if job:
        candidate_id = str((getattr(job, "generation_config", None) or {}).get("tageai_publish_candidate_id") or "").strip()
        if candidate_id:
            candidate = db.query(TageAiPublishCandidate).filter(
                TageAiPublishCandidate.candidate_id == candidate_id,
                TageAiPublishCandidate.tenant_id == invocation.tenant_id,
            ).first()
            version = db.query(ContentVersion).filter(
                ContentVersion.id == candidate.source_content_version_id,
                ContentVersion.tenant_id == invocation.tenant_id,
                ContentVersion.article_id.isnot(None),
            ).first() if candidate else None
        else:
            version = db.query(ContentVersion).filter(
                ContentVersion.job_id == job.id,
                ContentVersion.tenant_id == invocation.tenant_id,
                ContentVersion.article_id.isnot(None),
            ).order_by(ContentVersion.id.desc()).first()
        if version:
            article = db.query(Article).filter(
                Article.id == version.article_id,
                Article.tenant_id == invocation.tenant_id,
            ).first()
    attempts: list[PublishAttempt] = []
    if job is not None:
        attempts = db.query(PublishAttempt).filter(
            PublishAttempt.tenant_id == invocation.tenant_id,
            PublishAttempt.job_id == job.id,
        ).order_by(PublishAttempt.id.desc()).all()
    return job, article, attempts


def _converge_stale_generation(
    invocation: TageAiIntegrationInvocation,
    context: tuple[Optional[ContentJob], Optional[Article], list[PublishAttempt]],
) -> bool:
    """把没有活动心跳的内容生成任务收敛为明确的可重试失败。

    生成任务可能跨越模型、图片和视频服务，不能用 HTTP 请求超时直接判定失败；真实
    Worker 会在每个阶段边界写入 ``progress_snapshot.heartbeat_at``。只有任务仍处于
    ``generating``，且该心跳（或最后更新时间）超过容忍窗口时，才认定 Worker 已失活。
    有新心跳的任务完全不修改，避免把正常的长媒体任务误杀。
    """

    job, _, _ = context
    if job is None or job.status != "generating":
        return False

    snapshot = (getattr(job, "generation_config", None) or {}).get("progress_snapshot")
    heartbeat_at = None
    if isinstance(snapshot, dict):
        raw_heartbeat = snapshot.get("heartbeat_at")
        if isinstance(raw_heartbeat, str) and raw_heartbeat.strip():
            try:
                heartbeat_at = datetime.fromisoformat(raw_heartbeat.replace("Z", "+00:00"))
                if heartbeat_at.tzinfo is None:
                    heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            except ValueError:
                # 无法解析的心跳不能延长任务生命周期，继续使用数据库最后更新时间兜底。
                heartbeat_at = None

    last_activity = heartbeat_at or getattr(job, "updated_at", None) or invocation.started_at
    if last_activity is None:
        return False
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)

    from app.config import settings

    now = datetime.now(timezone.utc)
    timeout_seconds = max(60, int(settings.tageai_generation_heartbeat_timeout_seconds))
    if (now - last_activity).total_seconds() <= timeout_seconds:
        return False

    error_code = "GENERATION_WORKER_STALE"
    error_message = "内容生成 Worker 长时间没有心跳，任务已停止，可重新生成"
    job.status = "failed"
    job.error_code = error_code
    job.error_message = error_message
    invocation.status = "FAILED"
    invocation.phase = error_code
    invocation.progress = 100
    invocation.error_code = error_code
    invocation.error_message = error_message
    invocation.retryable = True
    invocation.finished_at = now
    logger.warning(
        "TaGeAI generation worker became stale: invocation=%s job=%s last_activity=%s",
        invocation.invocation_id,
        job.id,
        last_activity.isoformat(),
    )
    return True


def _converge_expired_relay_publish(
    invocation: TageAiIntegrationInvocation,
    context: tuple[Optional[ContentJob], Optional[Article], list[PublishAttempt]],
) -> bool:
    """在 Integration 查询路径收敛超时且无法查询最终状态的 relay 发布。

    判断依据是文章自身的 ``RELAY_PUBLISHING`` 阶段，而不是当前全局 relay 开关。
    这样即使部署在发布提交后切换了通道，历史 relay 任务也会继续按创建时的事实
    收敛。正常时限内不写入任何字段，完全保留已有 ``PUBLISHING`` 查询行为。
    """

    _, article, attempts = context
    if invocation.delivery_mode != "PUBLISH" or article is None:
        return False

    from app.config import settings
    from app.services.publish_delivery_state_service import expire_unresolved_relay_publish

    now = datetime.now(timezone.utc)
    if not expire_unresolved_relay_publish(
        article,
        attempts,
        now=now,
        timeout_seconds=settings.wechat_relay_publish_status_timeout_seconds,
    ):
        return False

    # 投递事实已被统一状态服务写成失败；Invocation 也必须同步保存诊断投影，避免
    # 后续查询仅靠瞬时计算而丢失 finishedAt、错误码或人工核验说明。
    latest_attempt = attempts[0] if attempts else None
    state = derive_invocation_state(
        context[0],
        article,
        invocation.delivery_mode,
        latest_attempt,
    )
    invocation.status = state["status"]
    invocation.phase = state["phase"]
    invocation.progress = state["progress"]
    invocation.result_data = state.get("result")
    invocation.error_code = state.get("error_code")
    invocation.error_message = state.get("error_message")
    invocation.retryable = state.get("retryable", False)
    invocation.finished_at = now
    logger.warning(
        "TaGeAI relay publish timed out during query: invocation=%s article=%s",
        invocation.invocation_id,
        article.id,
    )
    return True


def _serialize_invocation(
    invocation: TageAiIntegrationInvocation,
    db,
    context: Optional[tuple[Optional[ContentJob], Optional[Article], list[PublishAttempt]]] = None,
) -> dict:
    """将已加载的交付事实投影为 Gateway 稳定响应。

    ``context`` 允许查询路径在完成超时收敛后复用同一批 ORM 对象，既避免重复查询，
    也保证响应读取到刚刚写入的终态；其他既有调用方不传该参数时保持原有行为。
    """

    job, article, attempts = context or _load_invocation_delivery_context(invocation, db)
    # 查询按降序取得投递记录，首项是最新尝试，继续保留 PublishAttempt 优先于
    # Article 的既有判定，避免草稿投递失败被陈旧文章状态误报为成功。
    delivery_attempt = attempts[0] if attempts else None
    state = derive_invocation_state(job, article, invocation.delivery_mode, delivery_attempt) if job else _state(
        "FAILED", "FAILED", 100, error_code="INTERNAL_ERROR", error_message="关联内容任务不存在")
    # 经过持久化回调的终态故障信息优先保留，便于外部发布器补充细粒度错误码。
    if invocation.status == "FAILED" and invocation.error_code:
        state = _state("FAILED", invocation.phase or "FAILED", invocation.progress or 100,
                       result=invocation.result_data, error_code=invocation.error_code,
                       error_message=invocation.error_message or "外部调用失败", retryable=invocation.retryable)
    candidate = _ensure_preview_publish_candidate(db, invocation, job, article)
    if candidate is not None and state.get("result"):
        # 只回传不可猜测的候选引用与失效时间。账号主键、内容版本主键和候选状态属于
        # 平台内部审计事实，不能进入 Gateway、Renderer 或模型上下文。
        state["result"] = {
            **state["result"],
            "publishCandidateId": candidate.candidate_id,
            "publishCandidateExpiresAt": _iso(candidate.expires_at),
        }
    return {
        "invocation_id": invocation.invocation_id,
        "external_job_id": invocation.external_job_id,
        "status": state["status"],
        "phase": state["phase"],
        "progress": state["progress"],
        "platform": state.get("platform"),
        "platform_label": state.get("platform_label"),
        "media_summary": state.get("media_summary"),
        "estimated_remaining_seconds": state.get("estimated_remaining_seconds"),
        "heartbeat_at": state.get("heartbeat_at"),
        "result": state.get("result"),
        "error_code": state.get("error_code"),
        "error_message": state.get("error_message"),
        "retryable": state.get("retryable", False),
        "created_at": _iso(invocation.created_at),
        "started_at": _iso(invocation.started_at),
        "finished_at": _iso(invocation.finished_at),
    }


def _enqueue_callback_snapshot(
    db,
    invocation: TageAiIntegrationInvocation,
    snapshot: dict,
) -> bool:
    """把未出现过的状态快照写入可靠回调 outbox，返回是否创建了新事件。

    状态快照哈希故意不包含 eventId 和时间戳。这样 Beat 每次扫描都能安全重跑：只有文章
    生成、草稿投递、发布或失败状态确实变化时，才生成一条新的事件；网络重试只重发同一 eventId。
    """

    callback_state = {
        "invocationId": invocation.invocation_id,
        "externalJobId": snapshot.get("external_job_id"),
        "status": snapshot.get("status"),
        "phase": snapshot.get("phase"),
        "progress": snapshot.get("progress"),
        "result": snapshot.get("result"),
        "error": {
            "errorCode": snapshot.get("error_code"),
            "message": snapshot.get("error_message"),
            "retryable": bool(snapshot.get("retryable", False)),
        } if snapshot.get("error_code") else None,
    }
    # 平台进度字段只在存在时进入回调，保持旧状态快照的幂等哈希兼容；媒体槽位、资源
    # URL 和内部提示词不属于 Gateway 公开协议，绝不能借回调通道泄露。
    for source_key, output_key in (
        ("platform", "platform"),
        ("platform_label", "platformLabel"),
        ("media_summary", "mediaSummary"),
        ("estimated_remaining_seconds", "estimatedRemainingSeconds"),
        ("heartbeat_at", "heartbeatAt"),
    ):
        if snapshot.get(source_key) is not None:
            callback_state[output_key] = snapshot[source_key]
    snapshot_hash = hashlib.sha256(
        json.dumps(callback_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    exists = db.query(TageAiIntegrationCallbackOutbox.id).filter(
        TageAiIntegrationCallbackOutbox.invocation_id == invocation.id,
        TageAiIntegrationCallbackOutbox.snapshot_hash == snapshot_hash,
    ).first()
    if exists:
        return False

    event_time = datetime.now(timezone.utc).isoformat()
    payload = {
        **callback_state,
        "eventId": f"tageai-callback-{uuid.uuid4().hex}",
        "eventTime": event_time,
        "tenantBindingId": invocation.tenant_binding_id,
    }
    outbox = TageAiIntegrationCallbackOutbox(
        tenant_id=invocation.tenant_id,
        invocation_id=invocation.id,
        event_id=payload["eventId"],
        snapshot_hash=snapshot_hash,
        payload=payload,
        status="PENDING",
        attempt_count=0,
        next_attempt_at=datetime.now(timezone.utc),
    )
    try:
        # 并发扫描可能同时通过上面的 exists 查询。嵌套事务把唯一键冲突限制在这条
        # outbox 插入，不回滚调用状态本身，也不让同一快照产生多条可投递事件。
        with db.begin_nested():
            db.add(outbox)
            db.flush()
        return True
    except IntegrityError:
        return False


def resolve_target_account_binding(
    *,
    tenant_binding_id: Optional[str],
    target_account_ref: Optional[str],
    target_account_bindings: Optional[Mapping[str, object]],
    connector_account_lookup: Optional[Callable[[str], Optional[int]]] = None,
) -> int:
    """把 TaGeAI 的逻辑账号引用解析为本地公众号账号 ID。

    账号绑定是连接配置的一部分，不是调用参数。该函数故意不支持数字 ID 直通、默认
    账号或模糊匹配：调用方只能使用当前已验签租户绑定登记过的稳定引用，避免发布在
    账号增加、迁移或恶意请求后落到错误目标。
    """

    normalized_binding_id = str(tenant_binding_id or "").strip()
    normalized_account_ref = str(target_account_ref or "").strip()
    if not normalized_binding_id or not normalized_account_ref:
        raise IntegrationInputError("ACCOUNT_NOT_BOUND", "当前租户未绑定目标公众号账号")

    configured_account_id = target_account_bindings.get(normalized_account_ref) \
        if isinstance(target_account_bindings, Mapping) else None
    # bool 在 Python 中是 int 的子类，不能被当作合法主键；同样拒绝字符串数字，避免
    # 部署配置中的拼写错误默默把不可信值带入数据库查询。
    if isinstance(configured_account_id, int) and not isinstance(configured_account_id, bool) \
            and configured_account_id > 0:
        return configured_account_id

    # 新连接器账户由微信平台本身持久化，避免 TaGeAI 每新增一个公众号就必须修改环境变量。
    # 回退查询只接收已经验签的逻辑引用，且结果仍会在 _resolve_account 中复核租户和 active 状态。
    dynamic_account_id = connector_account_lookup(normalized_account_ref) if connector_account_lookup else None
    if isinstance(dynamic_account_id, int) and not isinstance(dynamic_account_id, bool) and dynamic_account_id > 0:
        return dynamic_account_id
    raise IntegrationInputError("ACCOUNT_NOT_BOUND", "目标公众号账号未绑定或不可用")


def lookup_connector_account_id(
    db: Any, *, tenant_id: int, owner_user_id: Optional[str], account_ref: str,
) -> Optional[int]:
    """从当前用户在当前租户的连接器账户记录解析内部账号 ID。

    逻辑引用只存在于 ``capabilities`` 的非敏感字段中，AppSecret 由凭据表独立保存。查询
    结果仍由调用方的 ``_resolve_account`` 验证 active 状态，防止停用账号在读取与创建间
    状态变化后被继续投递。
    """

    from app.integrations.tageai.connector_service import resolve_connector_account_id

    accounts = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == tenant_id,
        WeChatAccount.deleted_at.is_(None),
    ).all()
    if owner_user_id is None:
        return None
    return resolve_connector_account_id(
        accounts, tenant_id=tenant_id, owner_user_id=owner_user_id, account_ref=account_ref,
    )


def _resolve_account(db, tenant_id: int, account_id: int) -> WeChatAccount:
    """在本地数据库复核受控账号仍属于当前租户且可投递。"""

    query = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == tenant_id,
        WeChatAccount.status == "active",
        WeChatAccount.deleted_at.is_(None),
        WeChatAccount.id == account_id,
    )
    account = query.first()
    if not account:
        raise IntegrationInputError("ACCOUNT_NOT_BOUND", "目标公众号账号未绑定或不可用")
    return account


def _claim_preview_candidate(
    db,
    *,
    candidate_id: object,
    tenant_id: int,
    target_account_ref: str,
    account_id: int,
    publish_invocation_id: str,
) -> TageAiPublishCandidate:
    """在创建发布任务前占用已预览文章的唯一候选。

    读取使用行锁，以保证两个并发确认请求不会同时把一篇文章发布两次。领域函数负责
    租户、账号、期限和状态校验；这里再复核内部账号 ID，防止逻辑引用在连接器重绑后
    指向不同公众号。任何失败都发生在新 ContentJob 入队前，事务回滚后不会留下半成品
    发布任务。
    """

    normalized_candidate_id = str(candidate_id or "").strip()
    if not normalized_candidate_id:
        raise IntegrationInputError("MISSING_PUBLISH_CANDIDATE", "正式发布必须指定已预览的文章版本")
    candidate = db.query(TageAiPublishCandidate).filter(
        TageAiPublishCandidate.candidate_id == normalized_candidate_id,
    ).with_for_update().first()
    if candidate is None:
        raise IntegrationInputError("PUBLISH_CANDIDATE_NOT_FOUND", "发布候选不存在或已失效")
    if candidate.account_id != account_id:
        raise IntegrationInputError("PUBLISH_CANDIDATE_ACCOUNT_MISMATCH", "发布候选与当前公众号账号不匹配")
    try:
        claim_publish_candidate(
            candidate,
            tenant_id=tenant_id,
            target_account_ref=target_account_ref,
            publish_invocation_id=publish_invocation_id,
            now=datetime.now(timezone.utc),
        )
    except PublishCandidateError as exc:
        message = str(exc)
        if "租户" in message:
            code = "PUBLISH_CANDIDATE_TENANT_MISMATCH"
        elif "账号" in message:
            code = "PUBLISH_CANDIDATE_ACCOUNT_MISMATCH"
        elif "过期" in message:
            code = "PUBLISH_CANDIDATE_EXPIRED"
        else:
            code = "PUBLISH_CANDIDATE_CONSUMED"
        raise IntegrationInputError(code, message) from exc
    return candidate


def _validate_create_input(operation: str, delivery_mode: str, input_data: dict) -> None:
    """在创建 ContentJob 前校验 TaGeAI 调用的可消费输入。

    Gateway 是首层准入，但平台自身仍要在事务和队列边界前重复执行关键的类型校验。
    ``asset_ref`` 是跨系统资产句柄，当前没有经过租户授权的下载和正文解析链路；若让它
    入队，任务会先返回 202，随后才在生成上下文失败。Gateway 对外协议仅放行由平台
    安全抓取的 URL，避免原文或未解析的外部标识符进入任务快照、审计与持久化队列。
    """

    if operation not in {"generate", "imitate"}:
        raise IntegrationInputError("INVALID_PARAMETER", "operation 只支持 generate 或 imitate")
    if delivery_mode not in {"PREVIEW", "DRAFT", "PUBLISH"}:
        raise IntegrationInputError("INVALID_PARAMETER", "deliveryMode 只支持 PREVIEW、DRAFT 或 PUBLISH")
    if input_data.get("image_source") is not None and input_data.get("image_source") not in {"DASHSCOPE", "local", "erp"}:
        raise IntegrationInputError("INVALID_PARAMETER", "image_source 不是微信公众号平台支持的来源")
    if input_data.get("enabled_image_methods") is not None:
        methods = input_data.get("enabled_image_methods")
        if not isinstance(methods, list) or not methods or len(methods) > 3 or any(item not in {"DASHSCOPE", "LOCAL", "ERP"} for item in methods):
            raise IntegrationInputError("INVALID_PARAMETER", "enabled_image_methods 不是微信公众号平台支持的来源")
    if input_data.get("article_count") is not None and (not isinstance(input_data.get("article_count"), int) or not 1 <= input_data["article_count"] <= 20):
        raise IntegrationInputError("INVALID_PARAMETER", "article_count 超出微信公众号平台范围")
    if input_data.get("duration_sec") is not None and (not isinstance(input_data.get("duration_sec"), int) or not 1 <= input_data["duration_sec"] <= 3600):
        raise IntegrationInputError("INVALID_PARAMETER", "duration_sec 超出微信公众号平台范围")
    if operation == "generate" and not str(input_data.get("topic") or "").strip():
        raise IntegrationInputError("MISSING_TOPIC", "生成文章必须提供主题")
    if delivery_mode == "PUBLISH" and not str(input_data.get("publish_candidate_id") or "").strip():
        raise IntegrationInputError("MISSING_PUBLISH_CANDIDATE", "正式发布必须引用已生成的文章预览版本")

    # 预算校验必须发生在打开数据库会话和投递 Celery 前。这样超量请求会进入 Agent
    # 一次性确认交互，而不是先返回任务受理、再在生成阶段耗尽模型额度或长时间超时。
    try:
        normalize_generation_budget(input_data, budget_approval=input_data.get("budget_approval"))
    except GenerationBudgetError as exc:
        raise IntegrationInputError(
            exc.code,
            str(exc),
            details=(
                {
                    "requested": dict(exc.requested),
                    "default": dict(exc.default),
                    "hard_limit": dict(exc.hard_limit),
                }
                if isinstance(exc, BudgetApprovalRequired)
                else None
            ),
        ) from exc

    # ``reference`` 对 generate 是可选字段，对 imitate 是必填字段；但无论操作类型，
    # 一旦调用方传入参考，就不能允许无法由本平台解析的跨系统资产句柄或原文静默穿过。
    # 这样可以在数据库、ContentJob 与异步队列之前给 Gateway 一个可诊断的同步失败结果。
    reference = input_data.get("reference")
    if operation == "imitate":
        _validate_reference_input(
            reference,
            required=True,
            unsupported_message="当前仿写参考类型暂不支持",
        )
    elif reference is not None:
        _validate_reference_input(
            reference,
            required=False,
            unsupported_message="当前参考类型暂不支持",
        )


def _validate_reference_input(
    reference: Any,
    *,
    required: bool,
    unsupported_message: str,
) -> None:
    """校验外部调用中的参考输入是否能被当前平台安全消费。

    该函数只负责同步输入边界，不承担 URL 抓取或正文抽取。外部调用只允许 URL，避免
    文章全文被写入 Gateway 任务快照与审计日志；``asset_ref`` 也必须等待“TaGeAI
    资产授权下载 + 正文转换”的跨系统协议落地后才可放行。统一从这里拒绝，避免不同
    operation 产生不一致的持久化副作用。
    """

    if not isinstance(reference, dict) or not reference:
        if required:
            raise IntegrationInputError("MISSING_REFERENCE", "仿写文章必须提供受控参考")
        raise IntegrationInputError("INVALID_PARAMETER", "reference 必须包含 type 和 value")

    # Gateway 只传递 URL 引用，正文由微信平台的既有抓取链获取。不得把 TaGeAI 的
    # 外部资产句柄当作本地素材 ID，也不能把全文直接写入跨系统任务，避免授权边界和
    # 审计范围失控。
    reference_type = str(reference.get("type") or "").strip().lower()
    if reference_type != "url":
        raise IntegrationInputError("UNSUPPORTED_REFERENCE_TYPE", unsupported_message)

    if not str(reference.get("value") or "").strip():
        raise IntegrationInputError("MISSING_REFERENCE", "参考内容不能为空")


def _build_generation_config(
    operation: str,
    delivery_mode: str,
    input_data: dict,
    *,
    preview_candidate: Optional[TageAiPublishCandidate] = None,
) -> dict:
    """构造既有 ContentJob 可消费的配置，并冻结预览发布的内容版本。

    ``PREVIEW`` 不会落入草稿或正式发布分支；``PUBLISH`` 只有携带已锁定候选时才会
    创建 ``article_publish_existing`` 任务。Worker 因此能复用候选所指向的 Article，而不
    会把同一个主题重新生成成另一篇正文。
    """

    # 同一份预算快照同时用于正文截断、图片数量和视频时长。Worker 重试只读取该快照，
    # 不重新解释用户自然语言或再次扩大规模，确保一次性批准不会跨任务泄漏。
    budget = normalize_generation_budget(input_data, budget_approval=input_data.get("budget_approval"))
    config = {
        "article_count": input_data.get("article_count") or 1,
        "publish_mode": "direct" if delivery_mode == "PUBLISH" else "preview" if delivery_mode == "PREVIEW" else "draft",
        "style": input_data.get("style_notes") or "default",
        "tageai_operation": operation,
        "tageai_reference": input_data.get("reference"),
        "title_override": input_data.get("title_override"),
        "content_constraints": input_data.get("content_constraints") or [],
        "watermark_enabled": input_data.get("watermark_enabled"),
        "image_source": input_data.get("image_source"),
        "enabled_image_methods": input_data.get("enabled_image_methods"),
        "knowledge_base_ids": input_data.get("knowledge_base_ids"),
        "source_feed_id": input_data.get("source_feed_id"),
        "feed_article_ids": input_data.get("feed_article_ids"),
        "selected_image_urls": input_data.get("selected_image_urls"),
        "selected_cover_image_url": input_data.get("selected_cover_image_url"),
        "footer_template": input_data.get("footer_template"),
        "duration_sec": budget.video_duration_seconds or input_data.get("duration_sec"),
        "aspect_ratio": input_data.get("aspect_ratio"),
        "generation_budget": asdict(budget),
        "html_image_count": budget.image_count,
    }
    if preview_candidate is not None:
        config.update({
            "tageai_publish_candidate_id": preview_candidate.candidate_id,
            "source_content_version_id": preview_candidate.source_content_version_id,
            "source_article_id": preview_candidate.article_id,
        })
    return config


def _article_result(article: Article, delivery_mode: str) -> dict:
    """将已落库文章收敛为 Gateway 可消费的公开结果。

    ``contentRef`` 仅适用于平台内部追溯，桌面端没有权限也不应通过该引用再读取文章。
    因此正文生成成功后，在同一个受控结果中附带有限长度的只读预览。这里不返回账号
    主键、发布凭据或原始模型元数据，避免展示工件扩大为跨系统资源读取接口。
    """

    result = {
        "title": article.main_title or article.topic,
        "contentRef": f"wechat://articles/{article.id}",
    }
    preview_title = str(article.main_title or article.topic or "").strip()
    preview_content = str(getattr(article, "content", "") or "").strip()
    if preview_title and preview_content:
        # Electron 侧同样设有 50 KiB 上限。截断只影响预览展示，不改写文章或发布正文，
        # 防止异常长内容让状态同步和桌面渲染失去响应。
        preview = {
            "title": preview_title[:200],
            "content": preview_content[:50_000],
        }
        cover_image_url = str(getattr(article, "cover_image", "") or "").strip()
        if cover_image_url.startswith("https://") and len(cover_image_url) <= 2_048:
            preview["coverImageUrl"] = cover_image_url
        result["articlePreview"] = preview
    if article.publish_id:
        if delivery_mode == "DRAFT":
            result["draftId"] = str(article.publish_id)
        else:
            result["publishId"] = str(article.publish_id)
    if article.msg_data_id:
        result["articleUrl"] = str(article.msg_data_id)
    return result


def _ensure_preview_publish_candidate(
    db,
    invocation: TageAiIntegrationInvocation,
    job: ContentJob,
    article: Optional[Article],
) -> Optional[TageAiPublishCandidate]:
    """为已完成预览的文章创建或读取唯一发布候选。

    候选只在正文、文章和版本都已落库后生成，并与源 Invocation 一对一绑定。由于查询
    和回调都会调用序列化器，先查后建保证重复读取不会不断签发新候选；调用方的事务会
    把新候选与包含候选 ID 的状态回调一起提交。
    """

    if invocation.delivery_mode != "PREVIEW" or job is None or article is None:
        return None
    if job.status not in {"awaiting_review", "approved"} or article.status != "generated":
        return None
    version = db.query(ContentVersion).filter(
        ContentVersion.job_id == job.id,
        ContentVersion.tenant_id == invocation.tenant_id,
        ContentVersion.article_id == article.id,
    ).order_by(ContentVersion.id.desc()).first()
    if version is None:
        return None
    candidate = db.query(TageAiPublishCandidate).filter(
        TageAiPublishCandidate.source_invocation_id == invocation.id,
    ).first()
    if candidate is not None:
        return candidate
    candidate = TageAiPublishCandidate(
        candidate_id=f"wpc_{hashlib.sha256(invocation.invocation_id.encode('utf-8')).hexdigest()[:32]}",
        tenant_id=invocation.tenant_id,
        source_invocation_id=invocation.id,
        source_content_job_id=job.id,
        source_content_version_id=version.id,
        article_id=article.id,
        account_id=job.account_id,
        target_account_ref=str(invocation.target_account_ref),
        status="READY",
        expires_at=datetime.now(timezone.utc) + _PREVIEW_CANDIDATE_TTL,
    )
    db.add(candidate)
    db.flush()
    return candidate


def _article_failure_code(article: Article, delivery_mode: str) -> str:
    """从文章级失败事实恢复对外诊断码，并为历史泛化阶段提供兼容默认值。

    新的投递链路会将可行动的失败码保存到 ``Article.phase``；历史记录可能只有
    ``FAILED`` 或者仍残留非失败阶段。前者应按投递模式回退，后者不能作为错误码
    泄露给调用方，避免把“发布中”再次描述成终态失败原因。
    """

    phase = str(getattr(article, "phase", "") or "").strip()
    non_failure_phases = {
        "",
        "FAILED",
        "CONTENT_GENERATED",
        "DRAFT_SAVED",
        "PUBLISHED",
        "PUBLISHING",
        "RELAY_PUBLISHING",
    }
    if phase not in non_failure_phases:
        return phase
    return "DRAFT_DELIVERY_FAILED" if delivery_mode == "DRAFT" else "PUBLISH_SUBMISSION_FAILED"


def _state(status: str, phase: str, progress: int, result: Optional[dict] = None,
           error_code: Optional[str] = None, error_message: Optional[str] = None,
           retryable: bool = False, **public_progress_details: object) -> dict:
    """构造统一状态，并只允许调用方显式附加已收敛的公开进度字段。"""

    state = {
        "status": status,
        "phase": phase,
        "progress": progress,
        "result": result,
        "error_code": error_code,
        "error_message": error_message,
        "retryable": retryable,
    }
    for field in ("platform", "platform_label", "media_summary", "estimated_remaining_seconds", "heartbeat_at"):
        if public_progress_details.get(field) is not None:
            state[field] = public_progress_details[field]
    return state


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
