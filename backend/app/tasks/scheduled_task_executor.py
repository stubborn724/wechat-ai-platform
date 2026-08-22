"""Unified scheduled task executor — directly calls the same agent pipeline as article creation."""

import asyncio
import html
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta

from app.celery_app import celery_app
from app.config import settings
from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask, ScheduledTaskRun
from app.schemas.article import ImageResult
from app.services.publish_domain_policy import normalize_publish_domain
from app.services.scheduled_erp_image_policy import find_due_schedule_times

logger = logging.getLogger(__name__)


def configure_safe_console_output() -> None:
    """为定时任务进程配置安全的控制台输出编码。

    FastAPI 入口已经在 ``app.main`` 中处理过 Windows GBK 控制台问题，但 Celery
    Worker、本地脚本或测试可以直接导入本模块并执行任务入口。定时任务里包含大量
    中文和进度符号日志，若 stdout/stderr 仍是 GBK，普通 ``print`` 会抛出
    ``UnicodeEncodeError`` 并把业务任务误标为失败。这里在任务模块边界统一把
    不可编码字符替换输出，保证日志永远不能中断文章生成和草稿发布。
    """

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


configure_safe_console_output()

# 定时文章的生成和发布依赖多个外部服务。重试间隔采用显式有限序列，既给上游
# 足够恢复时间，也避免配置错误或永久故障造成无限调用和重复发布。
SCHEDULED_TASK_RETRY_DELAYS = (120, 300, 900)
SCHEDULED_TASK_MAX_ATTEMPTS = len(SCHEDULED_TASK_RETRY_DELAYS) + 1
# 消息已经写入数据库但尚未被 Worker 认领时，不需要等待长任务保护窗口。
# 该窗口大于 Beat 的一分钟扫描周期，又能在 Redis/Celery 消息丢失后及时补投。
SCHEDULED_QUEUED_STALE_SECONDS = 5 * 60
# retrying 状态的消息通常在等待下一次重试时间；使用短窗口即可接管没有回来
# 的消息。它与 running 的长窗口职责不同，不能共用 30 分钟。
SCHEDULED_RETRYING_STALE_SECONDS = 5 * 60
# HTML 仿写可能包含多轮正文生成和最多 20 张图。保护窗口必须大于正常长任务，
# 否则 Beat 会把仍在工作的 Worker 误判为丢失并启动并发副本；真正的失败重试仍
# 使用下方 2/5/15 分钟序列，二者职责不能混用。
SCHEDULED_RUN_STALE_SECONDS = 30 * 60


def get_scheduled_retry_delay(attempt_number: int) -> int:
    """按当前失败前的尝试次数取得下一次重试等待秒数。

    ``attempt_number`` 从 1 开始，超过配置序列时使用最后一个等待值；真正是否
    还能重试由 ``mark_scheduled_run_retry`` 的总次数判断，二者职责分开便于测试。
    """

    normalized_attempt = max(int(attempt_number or 1), 1)
    index = min(normalized_attempt - 1, len(SCHEDULED_TASK_RETRY_DELAYS) - 1)
    return SCHEDULED_TASK_RETRY_DELAYS[index]


def _iter_exception_chain(error: BaseException):
    """遍历异常及其 cause/context，识别被业务层包装过的网络错误。"""

    visited: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop(0)
        if id(current) in visited:
            continue
        visited.add(id(current))
        yield current
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
        nested_errors = getattr(current, "errors", None)
        if nested_errors:
            pending.extend(error for error in nested_errors if isinstance(error, BaseException))
        # 文生文链路的失败项带有提供商名称 ``(name, exception)``，不能直接把
        # 元组加入异常链。这里仅提取其中真实的异常，保持其他业务包装结构兼容。
        nested_failures = getattr(current, "failures", None)
        if nested_failures:
            for failure in nested_failures:
                nested_error = failure[1] if isinstance(failure, tuple) and len(failure) > 1 else failure
                if isinstance(nested_error, BaseException):
                    pending.append(nested_error)


def raise_scheduled_state_error(state) -> None:
    """把 Agent 状态错误还原为调度器可消费的领域异常。

    历史 Agent 通过 ``state.error`` 回传可展示文案。定时入口若直接包一层
    ``RuntimeError``，模型 JSON 解析这种可恢复错误便失去类型信息；新增的标记
    让 API 展示方式保持不变，同时由这里在真正进入调度边界时恢复异常语义。
    """

    message = str(getattr(state, "error", "") or "文章生成失败")
    if bool(getattr(state, "error_retryable", False)):
        from app.services.scheduled_retry_errors import RetryableModelOutputError

        raise RetryableModelOutputError(message)
    raise RuntimeError(message)


def is_retryable_scheduled_error(error: BaseException) -> bool:
    """判断定时任务异常是否适合自动重试。

    配置、认证、参数和知识库绑定错误重试也不会改变结果，因此直接落失败；
    网络超时、数据库短暂断连、ERP API 和图片提供商临时故障才进入有限重试。
    通过异常链判断可以保留发布服务对上游异常的原始分类，而不依赖字符串猜测。
    """

    try:
        import httpx
    except ImportError:
        httpx = None
    try:
        import requests
    except ImportError:
        requests = None
    try:
        from sqlalchemy.exc import DBAPIError, OperationalError
    except ImportError:
        DBAPIError = OperationalError = None
    try:
        from app.services.image_generation_models import (
            ImageErrorCategory,
            ImageProviderError,
        )
    except ImportError:
        ImageErrorCategory = ImageProviderError = None
    try:
        from app.services.image_generation_service import ImageGenerationFallbackError
    except ImportError:
        ImageGenerationFallbackError = None
    try:
        from app.services.erp_product_service import ErpProductApiError
    except ImportError:
        ErpProductApiError = None
    try:
        from app.services.wechat_publisher import WechatPublishAmbiguousError
    except ImportError:
        WechatPublishAmbiguousError = None
    try:
        from app.services.wechat_relay_client import WechatRelayPublishAmbiguousError
    except ImportError:
        WechatRelayPublishAmbiguousError = None
    try:
        from app.services.scheduled_retry_errors import RetryableScheduledTaskError
    except ImportError:
        RetryableScheduledTaskError = None

    retryable_image_categories = set()
    if ImageErrorCategory is not None:
        retryable_image_categories = {
            ImageErrorCategory.TEMPORARY,
            ImageErrorCategory.RATE_LIMIT,
            ImageErrorCategory.UPSTREAM,
            ImageErrorCategory.EMPTY_RESULT,
            ImageErrorCategory.TRUNCATED_RESPONSE,
        }

    # 不把所有 OSError 都视为网络故障：磁盘权限、文件不存在等本地错误重试只会
    # 重复消耗模型和发布配额。HTTP 客户端自己的连接异常已经单独列出。
    retryable_types = [TimeoutError, ConnectionError]
    if httpx is not None:
        retryable_types.extend((httpx.TimeoutException, httpx.NetworkError))
    if requests is not None:
        retryable_types.extend((requests.exceptions.Timeout, requests.exceptions.ConnectionError))

    exception_chain = list(_iter_exception_chain(error))
    # 发布请求已经发出但响应不明确时，微信可能已经产生了外部副作用。即使其
    # cause 是 requests.ConnectionError，也必须优先停止自动重试，交给人工核验。
    ambiguous_publish_error_types = tuple(
        error_type
        for error_type in (WechatPublishAmbiguousError, WechatRelayPublishAmbiguousError)
        if isinstance(error_type, type)
    )
    if ambiguous_publish_error_types and any(
        isinstance(current, ambiguous_publish_error_types)
        for current in exception_chain
    ):
        return False

    for current in exception_chain:
        # HTTP 认证、参数和权限错误属于永久失败；只有明确的限流、超时或服务端
        # 错误才值得再次请求。包装层（例如 ERP 的 ErpProductApiError）会通过
        # __cause__ 继续遍历到这里，因此不能按业务异常基类整体判定为可重试。
        response = getattr(current, "response", None)
        # MinIO/COS 等 SDK 有时把 HTTP 状态直接放在异常上，而不是 requests/httpx
        # 的 response 对象中。兼容两种形态后，临时 5xx 与限流可复用同一退避策略。
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            status_code = getattr(current, "status_code", None)
        if status_code in {408, 425, 429} or (
            isinstance(status_code, int) and status_code >= 500
        ):
            return True

        if isinstance(OperationalError, type) and isinstance(current, OperationalError):
            return True
        if (
            isinstance(DBAPIError, type)
            and isinstance(current, DBAPIError)
            and bool(getattr(current, "connection_invalidated", False))
        ):
            return True
        if isinstance(current, tuple(retryable_types)):
            return True
        storage_error_code = str(getattr(current, "code", "") or "").casefold()
        if storage_error_code in {
            "slowdown",
            "internalerror",
            "serviceunavailable",
            "requesttimeout",
            "operationtimedout",
        }:
            return True
        if (
            isinstance(RetryableScheduledTaskError, type)
            and isinstance(current, RetryableScheduledTaskError)
        ):
            return True
        if isinstance(ErpProductApiError, type) and isinstance(current, ErpProductApiError):
            # ERP 可能在 HTTP 200 中返回业务层“系统异常”。该属性由 ERP 领域层
            # 根据状态码和错误摘要冻结，调度器只负责执行统一的有限重试策略。
            return bool(getattr(current, "retryable", False))
        if isinstance(ImageProviderError, type) and isinstance(current, ImageProviderError):
            return getattr(current, "category", None) in retryable_image_categories
        if (
            isinstance(ImageGenerationFallbackError, type)
            and isinstance(current, ImageGenerationFallbackError)
        ):
            return any(is_retryable_scheduled_error(item) for item in current.errors)
    return False


def should_recover_scheduled_run(run: ScheduledTaskRun, *, now: datetime) -> bool:
    """判断执行记录是否已经超过安全窗口，可以由 Beat 补偿接管。

    ``queued`` 只有在已经产生投递尝试时才代表“消息可能丢失”；尚未派发的等待
    记录必须无限期留在数据库队列中，不能因为前一个长任务执行超过五分钟而被
    重复投递。新鲜的 ``running`` 记录仍使用长窗口，防止图片生成等正常长任务
    被并发复制。数据库锁和执行入口的消息 ID 校验会进一步阻止重复执行。
    """

    status = str(getattr(run, "status", "") or "").lower()
    if status == "retrying":
        next_retry_at = getattr(run, "next_retry_at", None)
        return bool(
            next_retry_at
            and now >= next_retry_at + timedelta(seconds=SCHEDULED_RETRYING_STALE_SECONDS)
        )
    if status == "queued":
        # attempt_count 表示 Worker 实际认领过的执行次数；初始等待记录为 0，
        # celery_task_id/next_retry_at 均为空。只有已经尝试投递的记录才允许进入
        # 丢消息补偿窗口，避免正常排队任务被错误地增加尝试次数。
        attempt_count = int(getattr(run, "attempt_count", 0) or 0)
        celery_task_id = str(getattr(run, "celery_task_id", "") or "").strip()
        dispatch_time = getattr(run, "next_retry_at", None)
        if attempt_count <= 0 and not celery_task_id and dispatch_time is None:
            return False
        # ``created_at`` 属于原始计划时段，Worker 丢失后重新入队仍可能是几天后；
        # 优先使用本次入队写入的 ``next_retry_at``，避免 Beat 刚派发就再次复制。
        reference_time = (
            dispatch_time
            or getattr(run, "started_at", None)
            or getattr(run, "created_at", None)
        )
    elif status == "running":
        reference_time = getattr(run, "started_at", None)
    else:
        return False
    stale_seconds = (
        SCHEDULED_QUEUED_STALE_SECONDS
        if status == "queued"
        else SCHEDULED_RUN_STALE_SECONDS
    )
    # 使用严格大于，避免刚好到达保护窗口边界时与 Beat 的下一次扫描并发
    # 触发两次补偿；下一分钟扫描会安全接管超过窗口的记录。
    return bool(reference_time and now - reference_time > timedelta(seconds=stale_seconds))


def is_scheduled_run_in_flight(run: ScheduledTaskRun) -> bool:
    """判断一条记录是否占用唯一的定时任务执行槽位。

    定时文章故意采用单 Worker 串行处理，数据库中状态是可靠队列的唯一依据：
    ``running`` 和 ``retrying`` 必须阻塞后续派发；``queued`` 只有已经写入投递
    时间或 Celery 消息 ID 时才是“在途消息”。初始 ``queued`` 记录只是等待，
    可以被调度器选为下一条队头。
    """

    status = str(getattr(run, "status", "") or "").lower()
    if status in {"running", "retrying"}:
        return True
    if status != "queued":
        return False
    return bool(
        int(getattr(run, "attempt_count", 0) or 0) > 0
        or str(getattr(run, "celery_task_id", "") or "").strip()
        or getattr(run, "next_retry_at", None) is not None
    )


def _scheduled_run_sort_key(run: ScheduledTaskRun) -> tuple[str, str, int]:
    """返回定时任务运行记录的稳定队列顺序。

    任务可能来自不同的定时配置，因此不能按某一个 task_id 分别排队；统一使用
    计划日期、计划时间和自增 ID 排序，保证同一分钟创建的记录也有确定顺序。
    """

    return (
        str(getattr(run, "scheduled_date", "") or ""),
        str(getattr(run, "scheduled_time", "") or ""),
        int(getattr(run, "id", 0) or 0),
    )


def select_next_waiting_scheduled_run(
    runs: list[ScheduledTaskRun],
) -> ScheduledTaskRun | None:
    """从活动运行记录中选择下一条真正等待派发的记录。

    队列是全局串行的：只要存在已经派发、正在运行或等待重试的记录，后续记录
    都必须留在数据库中等待。只有没有任何在途记录时，才选择最早的初始 queued
    记录；这一步是避免“第三个任务排队五分钟后被误补投”的核心边界。
    """

    active_runs = sorted(
        [
            run
            for run in runs
            if str(getattr(run, "status", "") or "").lower()
            in {"queued", "running", "retrying"}
        ],
        key=_scheduled_run_sort_key,
    )
    if any(is_scheduled_run_in_flight(run) for run in active_runs):
        return None

    for run in active_runs:
        if str(getattr(run, "status", "") or "").lower() == "queued":
            return run
    return None


def _load_active_scheduled_runs(db, *, lock: bool = False) -> list[ScheduledTaskRun]:
    """读取活动队列记录，并在调度关键区按需加数据库行锁。

    行锁让多个 Beat 或 API 线程同时检查队列时共享同一份队列视图；提交派发
    记录前，其他检查者不能同时把下一条任务也认领成在途消息。
    """

    query = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.status.in_(["queued", "running", "retrying"]))
        .order_by(
            ScheduledTaskRun.scheduled_date.asc(),
            ScheduledTaskRun.scheduled_time.asc(),
            ScheduledTaskRun.id.asc(),
        )
    )
    if lock:
        query = query.with_for_update()
    return query.all()


def mark_scheduled_run_retry(
    db,
    run: ScheduledTaskRun,
    error: BaseException,
    *,
    now: datetime | None = None,
) -> bool:
    """把一次异常转换成 retrying 或最终 failed，并持久化下一次时间。

    返回 ``True`` 表示调用方应继续执行 Celery retry；返回 ``False`` 表示错误
    不可恢复或已经达到总尝试次数。状态先落库再发消息，Worker 重启时不会丢失
    失败原因和重试边界。
    """

    now = now or datetime.utcnow()
    attempt_count = int(getattr(run, "attempt_count", 0) or 0)
    error_message = str(error)[:4000]
    if is_retryable_scheduled_error(error) and attempt_count < SCHEDULED_TASK_MAX_ATTEMPTS:
        delay = get_scheduled_retry_delay(attempt_count)
        run.status = "retrying"
        run.error_message = error_message
        run.next_retry_at = now + timedelta(seconds=delay)
        run.finished_at = None
        # Celery retry 会生成下一条消息；清除旧消息 ID，执行入口会为新尝试重新认领。
        run.celery_task_id = None
        db.commit()
        return True

    run.status = "failed"
    run.error_message = error_message
    run.next_retry_at = None
    run.finished_at = now
    db.commit()
    return False


def resolve_scheduled_publish_domain(task, run: ScheduledTaskRun | None = None) -> str:
    """解析一次定时运行实际使用的发布域。

    已创建的运行记录优先使用自己的快照，避免用户后来编辑任务配置时改变已
    排队时段；没有快照的历史运行才回退到任务字段，保证旧数据能够继续重试。
    """

    run_domain = getattr(run, "publish_domain", None) if run is not None else None
    task_domain = getattr(task, "publish_domain", None)
    return normalize_publish_domain(run_domain or task_domain)


def is_article_delivery_complete(
    article,
    publish_mode: str,
    publish_domain: str = "public",
) -> bool:
    """判断文章是否已经完成同一发布域的微信交付，供重试入口幂等短路。"""

    normalized_domain = normalize_publish_domain(publish_domain)
    article_domain = getattr(article, "publish_domain", None)
    # 历史文章没有快照时沿用旧的“有外部 ID 即视为已交付”规则；新文章若已
    # 记录域且与当前运行不同，则不能把公域结果当作私域结果复用。
    # 私域能力是本次新增的，历史直发布文章不可能是私域交付；只有草稿模式
    # 或历史默认公域可以兼容短路。
    if (
        not article_domain
        and publish_mode == "direct"
        and normalized_domain == "private"
    ):
        return False
    if article_domain and normalize_publish_domain(article_domain) != normalized_domain:
        return False
    status = str(getattr(article, "status", "") or "").lower()
    if publish_mode == "draft" and status == "draft_saved":
        return True
    if publish_mode == "direct" and status == "published":
        return True
    # 某些历史发布链路只持久化微信返回 ID；有 ID 就说明外部副作用已经发生，
    # 不能重新创建文章再发布一次。
    return bool(
        getattr(article, "publish_id", None)
        or getattr(article, "msg_data_id", None)
    )


def _enqueue_scheduled_run(
    db,
    task_id: int,
    run: ScheduledTaskRun,
    *,
    reason: str,
    allow_fresh: bool = False,
) -> bool:
    """为队头运行记录派发一次 Celery 消息。

    ``attempt_count`` 只统计 Worker 真正开始执行的次数，不能在消息投递前增加；
    否则 Broker 暂时不可用或任务仅仅排队时，会错误消耗“最大执行次数”。先把
    当前记录标成已派发，再发送消息，能让调度器在进程发送后立即崩溃时通过恢复
    窗口补投；消息 ID 最终写回数据库，用于识别同一消息的安全重投。
    ``allow_fresh`` 仅用于新计划时段的第一次派发；恢复路径必须重新检查过期窗口，
    这样多个 Beat 同时扫描时只有第一个恢复者能够成功入队。
    """

    now = datetime.utcnow()
    locked_run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.id == run.id, ScheduledTaskRun.task_id == task_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if locked_run is None:
        logger.warning("定时任务运行记录不存在 task_id=%s run_id=%s", task_id, run.id)
        return False
    run = locked_run
    if allow_fresh:
        # 新队头只能来自尚未派发的初始 queued 记录。即使调用方拿到的是
        # 旧查询结果，也不能在行锁内把已经被另一个 Beat 标记为在途的记录再次
        # 派发；这一步是数据库队列的并发闸门。
        if (
            str(getattr(run, "status", "") or "").lower() != "queued"
            or is_scheduled_run_in_flight(run)
        ):
            return False
    elif not should_recover_scheduled_run(run, now=now):
        # 多个 Beat 可能同时扫描到同一条过期记录。第一个事务提交后，后续
        # 进程必须重新检查保护窗口，不能因为旧查询结果继续派发重复消息。
        return False

    run.status = "queued"
    run.started_at = None
    run.finished_at = None
    # queued 状态也需要一个本次派发时间。字段名称沿用已有重试时间字段，避免
    # 为仅用于恢复保护窗口的时间戳再扩展一列；任务真正被领取后会清空它。
    run.next_retry_at = now
    run.celery_task_id = None
    run.error_message = reason[:4000]
    db.commit()

    try:
        # 这里由普通 Worker 中的检查任务发起，不能只依赖 Celery 的全局路由。
        # 在 Worker 重启后的积压恢复场景中，显式指定交换路由可确保文章执行消息
        # 始终进入专用 scheduled 队列，而不会出现数据库已标记派发、Worker 却
        # 没有收到消息的悬挂记录。
        async_result = execute_scheduled_article.apply_async(
            args=(task_id, run.id),
            queue="scheduled",
            routing_key="scheduled",
            retry=True,
        )
    except Exception as exc:
        # Broker 短暂不可用时也不能把记录留在 queued 假装已经派发；下一次
        # Beat 会在保护窗口后重新接管这条记录。
        run.status = "retrying"
        run.error_message = f"Celery 派发失败：{exc}"[:4000]
        # 派发失败不是一次内容执行失败，不消耗 attempt_count；恢复扫描会在
        # 延迟窗口后再次尝试同一条队头记录，后续任务继续留在数据库中等待。
        run.next_retry_at = now + timedelta(seconds=get_scheduled_retry_delay(
            int(getattr(run, "attempt_count", 0) or 0) + 1
        ))
        db.commit()
        logger.error("定时任务派发失败 task_id=%s run_id=%s: %s", task_id, run.id, exc)
        return False

    run.celery_task_id = getattr(async_result, "id", None)
    db.commit()
    logger.info(
        "已派发定时任务 task_id=%s run_id=%s attempt=%s reason=%s celery_id=%s",
        task_id,
        run.id,
        run.attempt_count,
        reason,
        run.celery_task_id,
    )
    return True


def _recover_stale_scheduled_runs(db, *, now: datetime | None = None) -> int:
    """补偿队头 Worker 中断、Broker 丢消息或进程重启留下的旧运行记录。

    恢复同样遵守单 Worker 队列约束：一次扫描最多重投一条消息；如果还有新鲜的
    在途记录，后面的历史记录不能被提前派发。初始 ``queued`` 记录不属于丢消息，
    由 ``_dispatch_next_waiting_scheduled_run`` 按顺序派发。
    """

    now = now or datetime.utcnow()
    runs = _load_active_scheduled_runs(db)
    # 任何新鲜在途记录都代表唯一 Worker 仍可能正在处理队头；不能因为另一个
    # 旧记录已经超过窗口，就把它也重新投递，避免队列里出现两个生成副本。
    if any(
        is_scheduled_run_in_flight(run)
        and not should_recover_scheduled_run(run, now=now)
        for run in runs
    ):
        return 0

    for run in runs:
        if not should_recover_scheduled_run(run, now=now):
            continue
        reason = (
            f"检测到 {run.status} 运行记录超过保护窗口，自动重新排入队头"
        )
        if _enqueue_scheduled_run(db, run.task_id, run, reason=reason):
            logger.warning(
                "已补偿定时任务队头 task_id=%s run_id=%s，后续记录继续等待",
                run.task_id,
                run.id,
            )
            return 1
        # 派发失败后记录会变成 retrying，仍然是队头；本轮不能继续派发下一条。
        return 0
    return 0


def _dispatch_next_waiting_scheduled_run(db) -> bool:
    """在全局有限槽位中派发可准入的等待记录。

    数据库行锁和单条记录的 ``_enqueue_scheduled_run`` 仍负责避免重复消息；本函数
    只把原来的全局单队头改为“不同任务最多两个并行槽”。同一任务始终只会选中
    一条，保证文章生成、防重和微信投递语义不变。
    """

    from app.config import settings
    from app.services.scheduled_run_admission_service import (
        select_admissible_scheduled_runs,
    )

    runs = _load_active_scheduled_runs(db, lock=True)
    admitted_runs = select_admissible_scheduled_runs(
        runs,
        max_active_runs=settings.scheduled_task_max_active_runs,
        is_in_flight=is_scheduled_run_in_flight,
    )
    dispatched = False
    for run in admitted_runs:
        if _enqueue_scheduled_run(
            db,
            run.task_id,
            run,
            reason="按受控并发定时任务顺序进入执行队列",
            allow_fresh=True,
        ):
            dispatched = True
    return dispatched


def _claim_scheduled_run(
    db,
    run: ScheduledTaskRun,
    *,
    celery_task_id: str | None,
    now: datetime | None = None,
) -> bool:
    """原子化认领一次执行，区分消息重投和并发重复消息。"""

    now = now or datetime.utcnow()
    locked_run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.id == run.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if locked_run is None:
        return False
    run = locked_run
    status = str(getattr(run, "status", "") or "").lower()
    current_task_id = str(celery_task_id or "").strip() or None
    recorded_task_id = str(getattr(run, "celery_task_id", "") or "").strip() or None

    # acks_late 重投的是同一个 Celery 消息 ID。原 Worker 已丢失时允许它继续，
    # 但不同消息不能同时处理一条新鲜 running 记录，否则会重复生成和发布。
    if status == "running":
        if recorded_task_id and recorded_task_id == current_task_id:
            run.started_at = now
            db.commit()
            return True
        if not should_recover_scheduled_run(run, now=now):
            return False

    attempt_count = int(getattr(run, "attempt_count", 0) or 0)
    # attempt_count 只在 Worker 真正认领新消息时递增。初次派发、失败重试和
    # Worker 中断恢复都可以先经过 queued 状态，但它们本身不是一次内容执行；
    # 在这里统一计数，才能让“最多四次执行”与“排队/补投次数”彻底分离。
    attempt_count += 1
    if attempt_count > SCHEDULED_TASK_MAX_ATTEMPTS:
        run.status = "failed"
        run.error_message = "定时任务超过最大尝试次数，已停止自动重试"
        run.next_retry_at = None
        run.finished_at = now
        db.commit()
        return False

    run.attempt_count = attempt_count
    run.status = "running"
    run.started_at = now
    run.next_retry_at = None
    run.celery_task_id = current_task_id
    db.commit()
    return True


def _bind_scheduled_run_article(db, run_id: int | None, article_id: int) -> bool:
    """在文章刚创建后立即绑定运行记录，给发布超时重试提供幂等锚点。

    返回值用于区分“已绑定并提交”和“运行记录不存在”。后者仍由调用方提交
    新文章，避免文章因为历史数据缺失而只停留在 session 的未提交状态。
    """

    if run_id is None:
        return False
    run = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()
    if run is None:
        return False
    run.article_id = article_id
    db.commit()
    return True


def _persist_scheduled_article(db, article, run_id: int | None = None) -> None:
    """持久化纯图片或视频文章，并在发布前建立运行记录关联。

    图文流程在创建 Article 后还要继续写入正文，所以单独保留其流水线；纯图片
    和视频则可以在生成完成处统一收口。先 flush 得到文章 ID，再绑定运行记录，
    可让发布阶段异常在下一次有限重试时复用同一篇文章，而不是重复生成素材。
    """

    db.add(article)
    db.flush()
    if not _bind_scheduled_run_article(db, run_id, article.id):
        # 手动调用或历史运行记录缺失时仍要落库文章；正常定时任务由上面的绑定
        # 方法完成提交，避免额外事务提交导致状态观察出现短暂空窗。
        db.commit()


def _scheduled_delivery_key(article_id: int, account_id: int) -> str:
    """构造按文章和公众号隔离的交付键，支持一个运行记录生成多篇文章。"""

    return f"{article_id}:{account_id}"


def _is_successful_scheduled_delivery(
    delivery_results: dict,
    *,
    article_id: int,
    account_id: int,
    publish_mode: str,
    publish_domain: str = "public",
) -> bool:
    """判断某篇文章是否已经在指定公众号完成同一种交付和发布域。"""

    normalized_domain = normalize_publish_domain(publish_domain)
    result = delivery_results.get(_scheduled_delivery_key(article_id, account_id))
    recorded_domain = result.get("publish_domain") if isinstance(result, dict) else None
    return bool(
        isinstance(result, dict)
        and result.get("status") == "success"
        and result.get("mode") == publish_mode
        # 旧结果没有域字段时只在草稿或默认公域下兼容复用；私域必须有新字段
        # 证明它确实经过了 follower_push，不能拿历史公域结果顶替。
        and (
            (
                recorded_domain is None
                and (
                    publish_mode == "draft"
                    or normalized_domain == "public"
                )
            )
            or (
                recorded_domain is not None
                and normalize_publish_domain(recorded_domain) == normalized_domain
            )
        )
    )


def _cleanup_cos_relay_objects(relay_service, object_keys: list[str]) -> None:
    """精确删除本次文章产生的 COS 临时对象。

    清理失败只记录告警，不覆盖图生图或公众号发布的主异常；对象键来自当前
    任务准备结果，不接受前缀，因此不会误删其他租户或其他运行批次的素材。
    """
    if relay_service is None:
        return
    for object_key in reversed(object_keys):
        try:
            relay_service.delete_object(object_key)
        except Exception as exc:
            logger.warning("COS 临时对象清理失败 key=%s: %s", object_key, exc)


async def _run_with_cos_cleanup(operation, relay_service, object_keys: list[str]):
    """无论文章流水线成功或失败，都在 finally 中释放 COS 中转对象。"""
    try:
        return await operation()
    finally:
        _cleanup_cos_relay_objects(relay_service, object_keys)


def _select_article_cover(state, full_content: str) -> str:
    """优先选择本次生成的第一张有效图片作为封面。

    ``state.images`` 能准确表达图片 Agent 的输出顺序，应优先于解析最终 HTML；
    只有旧流程没有图片元数据时才解析最终内容。最终发布正文以 HTML 为准，
    因此 HTML 图片必须优先于 Markdown 中可能残留的本地页脚或历史占位图。
    """
    for image in getattr(state, "images", []) or []:
        image_url = str(getattr(image, "url", "") or "").strip()
        if image_url:
            # 最终内容可能经过 HTML 序列化，签名 URL 中的 ``&`` 会变成
            # ``&amp;``；封面下载接口需要原始查询串，因此在边界统一还原。
            return html.unescape(image_url)

    html_match = re.search(
        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']',
        full_content or "",
        re.IGNORECASE,
    )
    if html_match:
        return html.unescape(html_match.group(1).strip())

    markdown_match = re.search(r'!\[.*?\]\((.*?)\)', full_content or "")
    return html.unescape(markdown_match.group(1).strip()) if markdown_match else ""


def is_completed_scheduled_run(run: ScheduledTaskRun | None) -> bool:
    """判断运行记录是否已成功交付，防止 Redis 重投重复创建草稿。

    Celery worker 重启或网络确认超时后，已确认的消息可能再次投递。仅当运行状态为
    ``completed`` 且已关联文章时才视为不可重执行，避免历史残缺记录被错误跳过。
    """
    return bool(
        run
        and str(getattr(run, "status", "")).lower() == "completed"
        and getattr(run, "article_id", None)
    )


@celery_app.task
def check_scheduled_tasks():
    """Periodic task: check scheduled tasks that need to execute now."""
    db = MysqlSessionLocal()
    try:
        import zoneinfo
        shanghai_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now_shanghai = datetime.now(shanghai_tz)
        # 数据库时间统一按 UTC 的无时区值保存；Beat 每分钟先补偿旧运行记录，
        # 再创建今天的新时段，保证 Worker 重启不会阻塞当天后续时间点。
        _recover_stale_scheduled_runs(db, now=datetime.utcnow())
        today = now_shanghai.date()
        day_of_week = today.weekday()
        current_hour_min = f"{now_shanghai.hour:02d}:{now_shanghai.minute:02d}"

        tasks = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.is_active == True,
                ScheduledTask.day_of_week.in_([day_of_week, -1]),
            )
            .all()
        )

        triggered = 0
        for task in tasks:
            if not task.publish_times:
                continue

            account_ids = task.account_ids or ([task.account_id] if task.account_id else [])
            if not account_ids:
                continue

            existing_times = {
                row[0]
                for row in db.query(ScheduledTaskRun.scheduled_time).filter(
                    ScheduledTaskRun.task_id == task.id,
                    ScheduledTaskRun.scheduled_date == today,
                ).all()
            }
            due_times = find_due_schedule_times(
                task.publish_times,
                now_shanghai.replace(tzinfo=None),
                existing_times,
                grace_minutes=5,
            )
            for schedule_time in due_times:
                from app.services.scheduled_template_rotation_service import (
                    resolve_rotation_profile_for_scheduled_slot,
                )

                rotation_profile_id, rotation_version = (
                    resolve_rotation_profile_for_scheduled_slot(
                        db,
                        task=task,
                        scheduled_date=today,
                        scheduled_time=schedule_time,
                    )
                )
                # 唯一约束与独立提交共同防止 API 后台线程和 Celery Beat 并发重复触发。
                run = ScheduledTaskRun(
                    task_id=task.id,
                    scheduled_date=today,
                    scheduled_time=schedule_time,
                    # 在入队时冻结发布域。任务后续编辑只影响新的时间段，不能
                    # 改变已经排队的公域/私域交付意图。
                    publish_domain=normalize_publish_domain(
                        getattr(task, "publish_domain", None)
                    ),
                    # 轮换模板在入队瞬间冻结；关闭轮换时保持空值，Worker 继续读取
                    # 任务上的历史单模板字段，确保旧任务无行为变化。
                    format_profile_id=rotation_profile_id,
                    template_rotation_version=rotation_version,
                    status="queued",
                )
                db.add(run)
                try:
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning("Task %d slot %s was already claimed or could not be created: %s", task.id, schedule_time, exc)
                    continue

                # 这里只创建可靠队列记录，不立即把所有到期任务一起发送到
                # Celery。扫描结束后由统一队头调度器只派发最早的一条，其他记录
                # 保持 attempt_count=0 的纯等待状态，前一个长任务多久都不影响它们。
                logger.info(
                    "已创建定时任务队列记录 task_id=%d scheduled_time=%s: %s",
                    task.id,
                    schedule_time,
                    (task.topic or task.name)[:60],
                )

        if _dispatch_next_waiting_scheduled_run(db):
            triggered = 1

        logger.info("Scheduled tasks: %d due tasks, %d jobs created", len(tasks), triggered)
        return {"tasks_checked": len(tasks), "jobs_created": triggered}

    except Exception as exc:
        logger.error("Scheduled task check failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=len(SCHEDULED_TASK_RETRY_DELAYS), default_retry_delay=120)
def execute_scheduled_article(self, task_id: int, run_id: int | None = None):
    """执行定时文章流水线，并把可恢复异常交给 Celery 有限重试。

    旧实现把异常转换成普通返回值，Celery 认为任务成功，导致声明的
    ``max_retries`` 永远不会生效。这里先持久化运行状态，再调用 ``self.retry``；
    最后一次或不可恢复错误才落为 failed，Beat 仍可识别 retrying 状态。
    """
    from app.models.mysql_models import Article, ScheduledTask as ST
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_service import create_article as create_article_record
    from app.services.wechat_publisher import publish_article
    from app.config import settings

    # 用量账本必须覆盖 ERP 选图、标题、正文、图生图和发布前处理的完整一次运行；
    # 当前先写入 Worker 日志，后续接入账单表时无需侵入各 Agent。
    from app.services.model_usage_service import (
        begin_model_usage_collection,
        end_model_usage_collection,
    )

    usage_token = begin_model_usage_collection(
        f"scheduled_task:{task_id}:run:{run_id or 'manual'}"
    )
    db = MysqlSessionLocal()
    try:
        task = db.query(ST).filter(ST.id == task_id).first()
        if not task:
            logger.error("Scheduled task %d not found", task_id)
            if run_id is not None:
                missing_task_run = db.query(ScheduledTaskRun).filter(
                    ScheduledTaskRun.id == run_id,
                    ScheduledTaskRun.task_id == task_id,
                ).first()
                if missing_task_run and not is_completed_scheduled_run(missing_task_run):
                    # 任务配置已被删除属于永久失败。必须立即收口，否则 Beat 会把这条
                    # 没有业务意义的孤儿记录按 stale queued 反复派发。
                    mark_scheduled_run_retry(
                        db,
                        missing_task_run,
                        ValueError(f"定时任务 {task_id} 不存在，无法执行"),
                    )
            return {"error": f"Task {task_id} not found"}

        # 任务可能由旧脚本创建而没有 ``created_by``。文章表的 user_id 是强外键，
        # 因此必须在整个运行开始时解析真实租户成员，并在所有后续步骤复用该身份。
        from app.services.scheduled_task_actor_service import resolve_scheduled_task_actor_id

        execution_actor_id = resolve_scheduled_task_actor_id(db, task)

        run = None
        if run_id is not None:
            run = db.query(ScheduledTaskRun).filter(
                ScheduledTaskRun.id == run_id,
                ScheduledTaskRun.task_id == task_id,
            ).first()
            if not run:
                return {"task_id": task_id, "error": f"Task run {run_id} not found"}
            if is_completed_scheduled_run(run):
                logger.info(
                    "跳过已完成的定时任务运行 task_id=%s run_id=%s article_id=%s",
                    task_id,
                    run.id,
                    run.article_id,
                )
                return {
                    "task_id": task_id,
                    "run_id": run.id,
                    "article_id": run.article_id,
                    "status": "skipped_completed",
                }
            if not _claim_scheduled_run(
                db,
                run,
                celery_task_id=getattr(getattr(self, "request", None), "id", None),
            ):
                logger.info(
                    "跳过正在执行的定时任务消息 task_id=%s run_id=%s celery_id=%s",
                    task_id,
                    run.id,
                    getattr(getattr(self, "request", None), "id", None),
                )
                return {
                    "task_id": task_id,
                    "run_id": run.id,
                    "status": "already_running",
                }

        publish_domain = resolve_scheduled_publish_domain(task, run)
        if run is not None and not getattr(run, "publish_domain", None):
            # 旧版本运行记录没有发布域字段，首次被 Worker 接管时补齐快照，
            # 后续重试始终使用同一域。迁移后的新记录不会进入该分支。
            run.publish_domain = publish_domain
            db.commit()

        # 用户没提供主题时，不给兜底值 — 让具体处理函数自行决定（仿写标题或回退任务名）
        topic = task.topic  # 可能为 None
        fallback_topic = task.name
        content_type = task.content_type or "article"
        account_ids = task.account_ids or ([task.account_id] if task.account_id else [])
        publish_mode = task.publish_mode or "draft"

        print(f"\n{'='*60}")
        print(f"  [定时任务 {task_id}] content_type={content_type} topic={topic or '(用户未设置)'}")
        print(f"  accounts={account_ids} mode={publish_mode} domain={publish_domain}")
        print(f"{'='*60}")

        # 如果上一次异常发生在微信调用之后、数据库最终状态提交之前，重试必须
        # 先识别已经交付的文章；否则同一时段会再次保存草稿或直接发布。
        if run is not None and run.article_id:
            existing_article = db.query(Article).filter(Article.id == run.article_id).first()
            if existing_article and is_article_delivery_complete(
                existing_article,
                publish_mode,
                publish_domain,
            ):
                run.status = "completed"
                run.error_message = None
                run.next_retry_at = None
                run.finished_at = datetime.utcnow()
                db.commit()
                return {
                    "task_id": task_id,
                    "run_id": run.id,
                    "article_id": existing_article.id,
                    "status": "skipped_already_delivered",
                }
            if (
                existing_article
                and existing_article.status == "generated"
                and (existing_article.full_content or existing_article.content)
            ):
                _publish_to_wechat(
                    db,
                    existing_article,
                    account_ids,
                    publish_mode,
                    task,
                    run=run,
                )
                _finalize_article_delivery(db, existing_article, publish_mode)
                article_id = existing_article.id
            else:
                # pending 文章没有可复用的外部交付结果，允许重新生成；保留该记录
                # 供诊断，但不再让它阻塞本次重试。
                article_id = None
        else:
            article_id = None

        # ========== 纯图片 ==========
        if article_id is not None:
            pass
        elif content_type in ("image", "pure_image"):
            article_id = _scheduled_image(
                db,
                task,
                topic,
                fallback_topic,
                account_ids,
                publish_mode,
                run=run,
            )

        # ========== 视频 ==========
        elif content_type == "video":
            article_id = _scheduled_video(
                db,
                task,
                topic,
                fallback_topic,
                account_ids,
                publish_mode,
                run=run,
            )

        # ========== 图文 ==========
        else:
            article_id = _scheduled_article(
                db,
                task,
                topic,
                fallback_topic,
                account_ids,
                publish_mode,
                run_id,
                execution_actor_id,
                run=run,
            )

        # 更新定时任务状态（db 可能因之前的异常处于 rollback 状态，捕获处理）
        try:
            task.total_generated = (task.total_generated or 0) + (task.articles_per_day or 1)
            task.last_run_at = datetime.utcnow()
            if run:
                run.status = "completed" if article_id else "failed"
                run.article_id = article_id
                run.error_message = None if article_id else "未生成可发布文章"
                run.finished_at = datetime.utcnow()
            db.commit()
        except Exception as update_exc:
            logger.warning("Failed to update task progress: %s", update_exc)
            try:
                db.rollback()
            except Exception:
                pass

        logger.info("Scheduled task %d completed", task_id)
        return {"task_id": task_id, "status": "completed"}

    except Exception as exc:
        logger.error("Scheduled task %d failed: %s", task_id, exc)
        import traceback
        traceback.print_exc()
        should_retry = False
        if run_id is not None:
            try:
                # 外部服务异常之前可能伴随 SQLAlchemy flush 失败。先回滚当前事务，
                # 才能查询并更新运行记录；否则 PendingRollbackError 会把失败状态
                # 也吞掉，Beat 只能看到一条永远 running 的旧记录。
                db.rollback()
                run = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()
                if run:
                    should_retry = mark_scheduled_run_retry(db, run, exc)
            except Exception:
                db.rollback()
        elif is_retryable_scheduled_error(exc):
            should_retry = True

        request = getattr(self, "request", None)
        request_retries = int(getattr(request, "retries", 0) or 0)
        if should_retry and request_retries < self.max_retries:
            retry_countdown = get_scheduled_retry_delay(
                int(getattr(run, "attempt_count", 1) or 1)
            )
            logger.warning(
                "Scheduled task %d will retry in %ss (attempt=%s/%s)",
                task_id,
                retry_countdown,
                getattr(run, "attempt_count", None),
                SCHEDULED_TASK_MAX_ATTEMPTS,
            )
            raise self.retry(exc=exc, countdown=retry_countdown)
        return {"task_id": task_id, "error": str(exc)}
    finally:
        db.close()
        usage = end_model_usage_collection(usage_token)
        logger.info(
            "模型用量汇总 scope=%s text_requests=%d input_tokens=%d "
            "output_tokens=%d total_tokens=%d image_requests=%d image_breakdown=%s",
            usage.scope,
            usage.text_request_count,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.image_request_count,
            list(usage.image_breakdown),
        )


def _scheduled_article(
    db,
    task,
    topic,
    fallback_topic,
    account_ids,
    publish_mode,
    run_id: int | None = None,
    execution_actor_id: int | None = None,
    run: ScheduledTaskRun | None = None,
):
    """图文类型：和创建文章完全相同的 agent 流水线"""
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_service import create_article as create_article_record
    from app.services.article_agent_service import (
        agent1_generate_title_options,
        agent2_generate_outline,
        agent3_generate_content,
        agent4_analyze_image_requirements,
        agent5_generate_images,
        merge_images_into_content,
    )
    from app.services.wechat_publisher import publish_article
    from app.config import settings
    from app.services.scheduled_erp_image_service import (
        parse_scheduled_erp_image_config,
        prepare_erp_images_for_scheduled_run,
    )
    from app.services.cos_image_relay_service import CosImageRelayService
    from app.services.scheduled_article_context_service import (
        ScheduledKnowledgeContextError,
        bind_product_context,
        ensure_product_name_in_title,
        load_required_knowledge_context,
        split_knowledge_prompt_context,
    )

    has_feed_source = task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id)
    has_knowledge_base = bool(task.knowledge_base_ids)
    if not topic and not has_feed_source and not has_knowledge_base:
        print(f"  ⚠️ 无主题、投喂源和知识库，跳过图文生成")
        return None

    erp_image_config = parse_scheduled_erp_image_config(task.erp_image_config)
    if erp_image_config and run_id is None:
        raise ValueError("ERP 分类配图只能通过已记录的定时时段执行")

    # 投喂源、ERP 和知识库的职责必须在加载内容前明确：投喂源始终可用于
    # 文章结构与文字风格，ERP 则会覆盖投喂源图片成为唯一视觉主体。将决策
    # 收敛到纯策略服务，可避免未来某个图片分支又绕过 ERP 优先级。
    from app.services.scheduled_image_routing_policy import resolve_scheduled_image_route

    image_route = resolve_scheduled_image_route(
        has_erp_product=erp_image_config is not None,
        has_feed_source=has_feed_source,
        has_knowledge_base=has_knowledge_base,
    )
    # 格式模板是测试任务的显式能力开关。未绑定时绝不查询/切换格式，确保线上
    # 绣蔓 ERP 仿写继续使用经过验证的原执行链路与提示词。
    from app.services.format_profile_task_policy import should_use_format_profile

    # 轮换任务使用运行记录冻结的模板；普通任务继续使用任务级模板。读取运行快照
    # 是重试一致性的关键，不能只按 task.format_profile_id 重新查询。
    effective_format_profile_id = (
        getattr(run, "format_profile_id", None) if run is not None else None
    ) or getattr(task, "format_profile_id", None)
    format_profile = None
    if effective_format_profile_id and (
        should_use_format_profile(task)
        or getattr(run, "template_rotation_version", None) is not None
    ):
        from app.models.mysql_models import ArticleFormatProfile

        format_profile = (
            db.query(ArticleFormatProfile)
            .filter(
                ArticleFormatProfile.id == effective_format_profile_id,
                ArticleFormatProfile.tenant_id == task.tenant_id,
            )
            .first()
        )
        if format_profile is None:
            raise ValueError("定时任务绑定的格式模板不存在，已停止生成")
    generated_article_id = None

    for slot_idx in range(task.articles_per_day or 1):
        print(f"\n  >>> 槽位 {slot_idx+1} (图文) <<<")

        # 她格是知识库驱动的原创企业服务文章。任务通常不设置固定主题，若任由
        # 模型从同一资料自由发挥，极易连续生成“AI 入企/协同/复盘”的泛化文章。
        # 在创建文章前冻结一个具体经营痛点，重试继续依据同一运行时段计算，既能
        # 保证正文深度，也不会让一次失败重试换掉已经开始生成的选题。
        effective_topic = topic or ""
        shege_constraints: list[str] = []
        from app.services.shege_pain_point_planning_service import (
            is_shege_enterprise_ai_style,
            load_recent_shege_topics,
            plan_shege_pain_point,
        )

        if is_shege_enterprise_ai_style(task.style):
            scheduled_at = datetime.now()
            if run is not None:
                try:
                    scheduled_at = datetime.combine(
                        run.scheduled_date,
                        datetime.strptime(run.scheduled_time, "%H:%M").time(),
                    )
                except (TypeError, ValueError):
                    # 历史运行记录可能没有规范时间字符串；此时退回当前时间只会
                    # 影响选题轮换，不会影响任务原有的发布时段或交付行为。
                    scheduled_at = datetime.now()
            recent_topics = load_recent_shege_topics(
                db,
                tenant_id=task.tenant_id,
                now=scheduled_at,
            )
            frozen_topic = ""
            if run is not None and getattr(run, "article_id", None):
                from app.models.mysql_models import Article

                previous_article = db.query(Article).filter(
                    Article.id == run.article_id,
                    Article.tenant_id == task.tenant_id,
                ).first()
                frozen_topic = str(
                    getattr(previous_article, "topic", "") or ""
                ).strip()
            pain_point_plan = plan_shege_pain_point(
                recent_topics=recent_topics,
                now=scheduled_at,
                frozen_topic=frozen_topic,
            )
            effective_topic = effective_topic or pain_point_plan.topic
            shege_constraints = list(pain_point_plan.constraints)
            if topic:
                # 用户主动填写主题时不应被自动选题覆盖，但仍必须遵守一题深挖和
                # 历史避重规则。第一条只替换焦点描述，其余规则保持统一。
                shege_constraints[0] = (
                    f"全文只能围绕“{effective_topic}”这一个具体经营痛点展开，"
                    "禁止扩写成泛泛的 AI 入企介绍或罗列多个问题。"
                )
            print(f"  🎯 她格痛点选题: {effective_topic}")

        # 1. 创建 Article 记录
        actor_id = execution_actor_id
        if actor_id is None:
            from app.services.scheduled_task_actor_service import resolve_scheduled_task_actor_id

            actor_id = resolve_scheduled_task_actor_id(db, task)

        article = create_article_record(
            db=db, user_id=actor_id, tenant_id=task.tenant_id,
            topic=effective_topic, style=task.style or "default",
            image_source=task.image_source or "dashscope",
            footer_template=task.footer_template,
        )
        # 文章创建后立刻绑定 run，而不是等整条流水线结束；如果生成或微信交付
        # 阶段中断，下一次重试可以复用已生成内容或识别已完成交付。
        _bind_scheduled_run_article(db, run_id, article.id)
        print(f"  文章创建: task_id={article.task_id}")

        # 2. 构建 ArticleState
        state = ArticleState(
            task_id=article.task_id,
            user_id=actor_id,
            tenant_id=task.tenant_id,
            topic=effective_topic,
            style=task.style or "default",
            enabled_image_methods=task.enabled_image_methods or ["DASHSCOPE"],
            footer_template=task.footer_template,
            content_constraints=shege_constraints,
            # 旧任务或迁移前对象没有该字段时回退到五张，避免改变既有成本策略。
            max_generated_images=max(1, min(getattr(task, "html_image_count", 5) or 5, 30)),
        )
        # ERP 路径中，投喂源只仿写文章结构与文案；产品图片和知识库背景是唯一
        # 视觉输入。该显式状态会传到 HTML 仿写 Agent，避免它再分析原文章图片。
        state.skip_reference_image_understanding = image_route.mode == "erp_knowledge_background"
        if format_profile and format_profile.render_mode == "html_slots":
            # 已保存的模板蓝图直接交给内容 Agent，既不重新分析原 HTML，也不把长
            # HTML/CSS 发送给文本模型；任务原先选择的投喂源仍可提供风格上下文。
            state.format_profile_payload = format_profile.template_payload
            state.format_profile_title_policy = format_profile.title_policy

        # 3. 加载投喂源（仿写模式）。无论图片来源为何，文章文本、HTML 结构和
        # 风格档案都必须保留；但 ERP 模式禁止把投喂源图片送入视觉理解或仿写。
        ref_image_urls = []
        if task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id):
            try:
                from app.models.mysql_models import FeedSource, FeedSourceArticle

                rotation_reference_article_id = (
                    format_profile.source_article_id
                    if (
                        format_profile is not None
                        and getattr(run, "template_rotation_version", None) is not None
                    )
                    else None
                )
                reference_article_ids = (
                    [rotation_reference_article_id]
                    if rotation_reference_article_id
                    else task.feed_article_ids
                )
                if reference_article_ids:
                    refs = db.query(FeedSourceArticle).filter(
                        FeedSourceArticle.id.in_(reference_article_ids),
                        FeedSourceArticle.body_markdown.isnot(None),
                    ).all()
                    if refs:
                        # 轮换模板绑定的来源文章决定本次 HTML 版式；普通任务继续
                        # 使用用户明确选中的第一篇文章，其他文章只提供语言风格。
                        state.reference_html = refs[0].body_html or None
                        ref_texts = []
                        for r in refs:
                            reference_context = _build_reference_article_for_imitation(
                                r.title or "参考文章",
                                r.body_markdown or "",
                            )
                            if reference_context:
                                ref_texts.append(reference_context)
                            # ERP 产品优先时只仿写文章，不读取投喂源图片。这样
                            # 图片模型只能收到 ERP 原图和知识库的背景规则。
                            if image_route.load_reference_visuals:
                                ref_image_urls.extend(re.findall(r'!\[.*?\]\((.*?)\)', r.body_markdown or ""))
                        state.reference_articles = ref_texts
                        print(f"  📄 已加载 {len(ref_texts)} 篇用户选中的参考文章，{len(ref_image_urls)} 张参考图片")
                        _load_layout_template(state, refs[0])

                    style_source_id = (
                        refs[0].feed_source_id
                        if rotation_reference_article_id and refs
                        else task.feed_source_id
                    )
                    if style_source_id:
                        src = db.query(FeedSource).filter(FeedSource.id == style_source_id).first()
                        if src and src.style_profile:
                            state.style_profile = src.style_profile
                            print(f"  🎯 已加载仿写风格: {src.name}")
                else:
                    source_ids = task.feed_source_ids or ([task.feed_source_id] if task.feed_source_id else [])
                    if source_ids:
                        sources = db.query(FeedSource).filter(
                            FeedSource.id.in_(source_ids)
                        ).all()
                        for s in sources:
                            if s and s.style_profile:
                                state.style_profile = s.style_profile
                                print(f"  🎯 已加载仿写风格: {s.name}")
                                break
                        refs = db.query(FeedSourceArticle).filter(
                            FeedSourceArticle.feed_source_id.in_(source_ids),
                            FeedSourceArticle.body_markdown.isnot(None),
                        ).order_by(FeedSourceArticle.id.desc()).limit(3).all()
                        if refs:
                            # 自动选取时同样只采用一篇文章的 DOM，避免跨文章拼接版式。
                            state.reference_html = refs[0].body_html or None
                            ref_texts = []
                            for r in refs:
                                # 保留 [IMAGE:] 标记和完整正文，让 AI 能看到排版格式
                                body = r.body_markdown or ""
                                reference_context = _build_reference_article_for_imitation(
                                    r.title or "参考文章",
                                    body,
                                )
                                if reference_context and len(reference_context) > 50:
                                    ref_texts.append(reference_context)
                                    # 参考图片属于“AI 视觉仿写”专属输入，不能与 ERP
                                    # 产品图混用，否则模型会错误替换产品主体或背景规则。
                                    if image_route.load_reference_visuals:
                                        ref_image_urls.extend(re.findall(r'!\[.*?\]\((.*?)\)', body))
                            state.reference_articles = ref_texts
                            print(f"  📄 已加载 {len(ref_texts)} 篇参考文章，{len(ref_image_urls)} 张参考图片")
                        _load_layout_template(state, refs[0])
            except Exception as exc:
                print(f"  ⚠️ 加载投喂源失败: {exc}")

        # 4. 异步运行 Agent 流水线。ERP 产品和知识库会在标题 Agent 之前准备，
        # 因为产品名必须同时约束标题、正文和图片，而不是等图片槽位出现后才选图。
        relay_service = CosImageRelayService() if erp_image_config else None
        relay_object_keys: list[str] = []

        def _run_pipeline(init_state):
            import asyncio

            async def _run():
                s = init_state

                async def _prepare_product_and_knowledge_context() -> None:
                    """一次性准备整篇文章共用的 ERP 产品与品牌知识库上下文。

                    产品必须在标题生成前选定；知识库采用任务绑定的完整品牌规则，
                    不再依赖可能为空的主题向量检索。任何一项缺失都会停止发布，避免
                    生成一篇只有产品图、没有指定背景规则的文章。
                    """

                    product_name = ""
                    if erp_image_config:
                        prepared_images = await prepare_erp_images_for_scheduled_run(
                            db=db,
                            task_id=task.id,
                            tenant_id=task.tenant_id,
                            run_id=run_id,
                            config=erp_image_config,
                            # 一个 ERP 产品驱动整篇 4～5 张图片，防重记录也只占用一张原图。
                            requested_count=1,
                            relay_service=relay_service,
                        )
                        prepared_image = prepared_images[0]
                        relay_object_keys.append(prepared_image.relay_object_key)
                        s.reference_image_url = prepared_image.reference_url
                        s.reference_image_bytes = prepared_image.reference_image_bytes
                        s.reference_content_type = prepared_image.reference_content_type
                        # ERP 可能只返回产品编号。展示名在标题、正文和图片 Agent 之间
                        # 必须保持一致，因此在选定唯一主图后只识别一次，再统一绑定。
                        from app.services.erp_product_naming_service import (
                            enrich_erp_product_display_name,
                        )

                        # 产品一旦选定就同步冻结空间规则。规则来自 ERP 名称、分类和
                        # 标签的确定性匹配，不增加模型调用；后续标题、HTML 图片槽位
                        # 和最终图生图提示词都复用这一份快照，避免同一篇文章出现餐桌、
                        # 沙发等互相冲突的空间语义。
                        from app.services.scheduled_product_scene_service import (
                            resolve_product_scene_profile,
                        )

                        product_scene_profile = resolve_product_scene_profile(
                            prepared_image.product.name,
                            # ERP 的旧商品常只返回型号，但同一原图已归档时素材库
                            # 保存过品类标签。两者合并后再决定场景，视觉识别额度
                            # 耗尽也能稳定识别“屏风”等历史产品，避免对外显示
                            # “未识别家具”。
                            tags=[
                                *(prepared_image.product.tags or []),
                                *prepared_image.asset_taxonomy_tags,
                            ],
                            categories=prepared_image.product.categories,
                        )
                        # 视觉模型仅用于细化纯编号 ERP 商品的展示名。分类和标签已经
                        # 能确定产品所在空间，故先解析一次并作为命名降级值；额度耗尽
                        # 或服务不可用时不再退化成“家具单品”，也不额外增加模型调用。
                        product_name = await enrich_erp_product_display_name(
                            product_name=prepared_image.product.name,
                            image_url=prepared_image.reference_url,
                            # ``label`` 可能同时表达品类和房间，例如“茶几/边几/客厅”。
                            # 展示名只能保留第一个明确产品品类，不能把房间词拼入标题。
                            fallback_category=str(product_scene_profile.label).split("/", 1)[0],
                        )
                        s.product_scene_profile = product_scene_profile.to_payload()
                        s.product_brand_key = erp_image_config.source_key
                        print(
                            f"  🖼️ ERP 配图: {erp_image_config.commodity_category or '全部分类'}，"
                            f"已选择产品“{product_name}”作为图生图参考，"
                            f"近 {erp_image_config.repeat_after_days} 天不重复，"
                            f"场景={product_scene_profile.label}/{product_scene_profile.required_rooms[0]}"
                        )

                    article_context = ""
                    image_context = ""
                    if task.knowledge_base_ids:
                        from app.database import PgSessionLocal

                        pg_db = PgSessionLocal()
                        try:
                            full_knowledge_context = load_required_knowledge_context(
                                db=pg_db,
                                knowledge_base_ids=task.knowledge_base_ids,
                                tenant_id=task.tenant_id,
                            )
                        finally:
                            pg_db.close()
                        # 知识库中的文章格式与产品背景属于不同 Agent 的输入。
                        # 在这里一次拆分，避免正文和图片生成阶段各自截断或重复
                        # 解析同一份完整资料，既节省 token，也保证职责边界一致。
                        prompt_contexts = split_knowledge_prompt_context(full_knowledge_context)
                        article_context = prompt_contexts.article_context
                        image_context = prompt_contexts.image_context
                        print(
                            f"  📚 已加载知识库: {task.knowledge_base_ids}，"
                            f"文章规则 {len(article_context)} 字符，"
                            f"图片背景规则 {len(image_context)} 字符"
                        )
                    elif erp_image_config:
                        raise ScheduledKnowledgeContextError(
                            "ERP 定时文章必须绑定知识库，任务已停止发布"
                        )

                    if product_name:
                        bind_product_context(
                            state=s,
                            product_name=product_name,
                            configured_topic=topic,
                            article_context=article_context,
                            image_context=image_context,
                            # 投喂源已定义本篇的文章结构，只由知识库约束 ERP
                            # 产品图的场景与背景；非投喂源模式仍要求完整格式规则。
                            require_article_context=not has_feed_source,
                        )
                    elif article_context or image_context:
                        # 非 ERP 的旧任务也按相同边界注入，避免投喂源图文仿写
                        # 在图片提示词里重复消耗文章版式规则。
                        s.kb_context = article_context
                        s.image_prompt_context = image_context

                await _prepare_product_and_knowledge_context()

                # 纯海报格式必须由任务显式开启。知识库只提供规则，不拥有改变
                # 历史任务输出格式的权限；旧对象没有新字段时也按 standard 处理。
                layout_mode = getattr(task, "layout_mode", "standard") or "standard"
                profile_uses_poster_renderer = bool(
                    format_profile and format_profile.render_mode == "poster_gallery"
                )
                if (layout_mode == "seamless_poster" or profile_uses_poster_renderer) and not task.knowledge_base_ids:
                    raise ScheduledKnowledgeContextError(
                        "无缝海报任务必须绑定包含海报格式规则的知识库"
                    )
                if (layout_mode == "seamless_poster" or profile_uses_poster_renderer) and task.knowledge_base_ids:
                    from app.database import PgSessionLocal
                    from app.services.brand_knowledge_routing import (
                        resolve_brand_knowledge_base_ids_for_task,
                    )
                    from app.services.image_generation_service import image_generation_service
                    from app.services.poster_article_service import (
                        generate_poster_images,
                        generate_poster_plan,
                    )
                    from app.services.publication_format_service import (
                        load_publication_format_from_knowledge_bases,
                        render_poster_gallery_html,
                    )
                    from app.services.format_profile_service import (
                        apply_poster_template_to_publication_profile,
                    )
                    from app.services.scheduled_publication_policy import (
                        should_use_poster_layout,
                    )
                    from app.services.scheduled_product_scene_service import (
                        product_scene_profile_from_payload,
                    )
                    from app.services.scheduled_image_quality_service import (
                        inspect_generated_image_url,
                    )

                    poster_knowledge_base_ids = list(task.knowledge_base_ids or [])
                    pg_db = PgSessionLocal()
                    try:
                        # ERP 来源键是海报背景的品牌边界。任务历史上可能只绑定了
                        # 背景库，运行时补齐同品牌格式库即可识别纯海报规则，避免
                        # 要求运营人员手工修改旧任务，也不会把其他品牌规则混入。
                        poster_knowledge_base_ids = resolve_brand_knowledge_base_ids_for_task(
                            db=pg_db,
                            tenant_id=task.tenant_id,
                            source_key=erp_image_config.source_key if erp_image_config else None,
                            configured_ids=poster_knowledge_base_ids,
                        )
                        publication_profile = load_publication_format_from_knowledge_bases(
                            db=pg_db,
                            knowledge_base_ids=poster_knowledge_base_ids,
                            tenant_id=task.tenant_id,
                        )
                    finally:
                        pg_db.close()

                    use_poster_layout = should_use_poster_layout(
                        "seamless_poster" if profile_uses_poster_renderer else layout_mode,
                        publication_profile,
                    )
                    if not use_poster_layout:
                        raise ScheduledKnowledgeContextError(
                            "任务已选择无缝海报，但知识库未识别到纯海报格式规则"
                        )

                    if use_poster_layout:
                        poster_text_overlay_enabled = bool(
                            format_profile
                            and isinstance(format_profile.template_payload, dict)
                            and format_profile.template_payload.get(
                                "poster_text_overlay_mode"
                            )
                            == "programmatic_text_v1"
                        )
                        if profile_uses_poster_renderer:
                            # 模板只覆盖连续图片数量；知识库仍提供品牌视觉、文案和
                            # 页脚。两者组合保持当前无缝海报的输出质量与零缝隙 HTML。
                            publication_profile = apply_poster_template_to_publication_profile(
                                publication_profile,
                                format_profile.template_payload,
                            )
                        # 产品名在准备阶段已写入 ``ArticleState``，海报分支不能
                        # 引用准备函数的局部变量，否则异步边界外会出现未定义错误。
                        if not s.product_name:
                            raise ScheduledKnowledgeContextError(
                                "纯海报定时任务必须配置 ERP 产品图片来源"
                            )
                        print(
                            "  🧩 发布格式: 纯海报拼接，"
                            f"{publication_profile.poster_count + 1} 张"
                            f"{'正文型内容海报' if poster_text_overlay_enabled else '海报'}"
                        )
                        s.footer_template = publication_profile.footer_template
                        # 标题和图片使用同一份已冻结的产品场景快照。ERP 名称退化为
                        # 型号时，标题可使用已确认的品类（如茶几），不把内部编号带到草稿。
                        poster_scene_profile = product_scene_profile_from_payload(
                            s.product_scene_profile,
                            product_name=s.product_name,
                        )
                        poster_plan = await generate_poster_plan(
                            profile=publication_profile,
                            product_name=s.product_name,
                            title_subject=poster_scene_profile.label,
                            brand_key=erp_image_config.source_key if erp_image_config else None,
                            # 公共写作模板同时约束公众号标题；未设置时保持既有海报
                            # 标题链路，确保正式运行中的绣蔓任务不受影响。
                            style=task.style,
                            # 新三品牌的程序叠字模板要求三张都承载正文信息；历史
                            # 海报任务保留标题海报行为，避免影响已经正式运行的绣蔓。
                            body_copy_only=poster_text_overlay_enabled,
                        )
                        poster_urls = await generate_poster_images(
                            profile=publication_profile,
                            plan=poster_plan,
                            product_name=s.product_name,
                            tenant_id=s.tenant_id,
                            # 海报链路同时传入 ERP 原图字节和临时 HTTPS 地址：支持
                            # multipart 的提供商使用字节，URL 型备用模型使用 HTTPS 地址。
                            reference_image_url=s.reference_image_url,
                            reference_image_bytes=s.reference_image_bytes,
                            reference_content_type=s.reference_content_type,
                            product_scene_profile=poster_scene_profile,
                            generate_image=image_generation_service.generate,
                            embed_copy_in_model=not poster_text_overlay_enabled,
                            quality_checker=inspect_generated_image_url,
                        )
                        s.title = SelectedTitle(
                            main_title=poster_plan.article_title,
                            sub_title="",
                        )
                        s.images = [
                            ImageResult(
                                position=index,
                                url=image_url,
                                method="poster_gallery",
                                keywords=poster_plan.posters[index - 1].copy,
                                section_title=poster_plan.posters[index - 1].scene,
                            )
                            for index, image_url in enumerate(poster_urls, start=1)
                        ]
                        s.content = render_poster_gallery_html(
                            image_urls=poster_urls,
                            footer_template=publication_profile.footer_template,
                            poster_copies=[poster.copy for poster in poster_plan.posters],
                            programmatic_text_overlay=poster_text_overlay_enabled,
                            body_copy_only=poster_text_overlay_enabled,
                        )
                        s.full_content = s.content
                        return s

                # HTML 模板把公众号标题、首屏标题和正文槽位合并为同一次文本调用。
                # 只有显式模板任务跳过标题候选 Agent；正式 ERP 任务保持原调用顺序。
                if s.format_profile_payload:
                    s.title = SelectedTitle(
                        main_title=s.topic or "公众号文章",
                        sub_title="",
                    )
                else:
                    # Agent 1: 标题 — 返回 ArticleState
                    s = await agent1_generate_title_options(s)
                    if s.error:
                        raise_scheduled_state_error(s)
                    selected_title = (
                        s.title_options[0]
                        if s.title_options
                        else SelectedTitle(
                            main_title=s.topic or (s.reference_articles[0] if s.reference_articles else ""),
                            sub_title="",
                        )
                    )
                    s.title = (
                        ensure_product_name_in_title(selected_title, s.product_name)
                        if s.product_name else selected_title
                    )

                # HTML 仿写已经锁定真实 DOM 槽位、顺序与目标长度。独立大纲既不
                # 改变槽位，也不会作为最终内容落库，只会额外产生一次文生文调用。
                # 因此该模式直接进入槽位内容 Agent；普通 Markdown/知识库任务仍
                # 保留大纲步骤，保证自由文章的结构完整性。
                if not s.reference_html and not s.format_profile_payload:
                    s = await agent2_generate_outline(s)
                    if s.error:
                        raise_scheduled_state_error(s)

                # Agent 3: 正文或 HTML 槽位内容
                s = await agent3_generate_content(s)
                if s.error:
                    raise_scheduled_state_error(s)

                # 配图: 有参考图片时走理解+AI生成，否则走 AI 生图
                # 注意: 有 layout_template 时，agent 3 已按模板生成 [IMAGE:] 占位符
                # agent4/5 直接解析并配图即可
                from app.services.image_generation_service import is_image_generation_configured

                if is_image_generation_configured():
                    if 'data-ai-image-slot=' in (s.content or "") and s.image_requirements:
                        # HTML 仿写已完成格式、文字和图片需求分析，直接按原 img 节点
                        # 生成并回填图片，避免旧的 Markdown 占位符路径打乱图文位置。
                        s = await agent5_generate_images(s)
                        merge_images_into_content(s)
                    elif s.layout_template and s.content_blocks:
                        # 路径①: 结构化模板 → 从 blocks 提取图片需求 → 配图 → 渲染
                        from app.services.article_agent_service import (
                            extract_image_slots_from_blocks,
                            render_final_content,
                        )
                        from app.schemas.article import ImageRequirement

                        image_slots, s.content_blocks = extract_image_slots_from_blocks(s.content_blocks)

                        s.image_requirements = [
                            ImageRequirement(
                                position=slot["position"],
                                type="inline",
                                keywords=slot["requirement"],
                                prompt=slot["requirement"],
                                image_source="DASHSCOPE",
                                placeholder_id=slot["slot_id"],
                            )
                            for slot in image_slots
                        ]

                        s = await agent5_generate_images(s)

                        slot_urls = {}
                        for i, img in enumerate(s.images):
                            if img.url and i < len(image_slots):
                                slot_urls[image_slots[i]["slot_id"]] = img.url

                        s.full_content = render_final_content(
                            s.content_blocks,
                            slot_urls,
                            footer_template=s.footer_template or "",
                        )
                    elif image_route.mode == "reference_visual_imitation" and ref_image_urls:
                        # 路径②：未选择 ERP 的投喂源任务才允许理解和仿写参考图片。
                        await _gen_images_from_references(s, ref_image_urls)
                        merge_images_into_content(s)
                    else:
                        # 路径③：ERP 路径的 state 已带 ERP 产品原图字节和知识库
                        # 背景规则，Agent 5 会执行图生图；普通路径继续文生图。
                        s = await agent4_analyze_image_requirements(s)
                        s = await agent5_generate_images(s)
                        merge_images_into_content(s)
                        # 她格的原创图文没有 HTML 图片槽位。图片生成成功后必须按
                        # Agent 4 的章节需求写回 Markdown 正文，不能只保留在状态对象
                        # 中导致草稿只显示文字。服务内部按模板 ID 严格隔离，绣蔓及
                        # 所有仿写任务不会进入这条新路径。
                        from app.services.original_article_image_service import (
                            insert_original_article_images,
                            should_insert_original_article_images,
                        )

                        if should_insert_original_article_images(task.style):
                            s.full_content = insert_original_article_images(
                                s.full_content or s.content or "",
                                s.images,
                            )
                            s.content = s.full_content
                else:
                    s.full_content = s.content or ""

                return s

            return asyncio.run(_run_with_cos_cleanup(_run, relay_service, relay_object_keys))

        state = _run_pipeline(state)

        # 最终发布前以 HTML 为唯一真相收口所有正文图片。不能只处理 state.images：
        # HTML 仿写模板、封面或重试链路可能存在额外 img 节点。固定页脚二维码由
        # 服务自动识别并跳过，其他任一图片归档失败都会中止发布，杜绝混用版本。
        from app.services.article_publication_polish_service import (
            append_ai_image_disclaimer,
            normalize_final_article_images_with_attribution,
        )
        from app.services.scheduled_image_normalization_service import (
            CANONICAL_SCHEDULED_IMAGE_SIZE,
            SCHEDULED_WATERMARK_FONT_SIZE,
        )

        # 当前用户确认的 1024×1365 + 24px 规格只应用于普通 ERP 定时图文。
        # 无缝海报有自己的整套切片尺寸，不能被普通文章画布规则覆盖；普通投喂源
        # 文章也保持原始尺寸，避免这次视觉优化污染已有格式任务。
        use_fixed_erp_image_policy = (
            image_route.mode == "erp_knowledge_background"
            and (getattr(task, "layout_mode", "standard") or "standard")
            != "seamless_poster"
        )

        normalized_images = asyncio.run(
            normalize_final_article_images_with_attribution(
                db,
                content=state.full_content or state.content or "",
                tenant_id=task.tenant_id,
                # ERP 任务使用已识别的产品名；非 ERP 图文以最终标题作为署名，
                # 保证所有正文图片都有可读的业务归属。
                product_name=state.product_name or (
                    state.title.main_title if state.title else state.topic
                ),
                target_size=(
                    CANONICAL_SCHEDULED_IMAGE_SIZE
                    if use_fixed_erp_image_policy
                    else None
                ),
                watermark_font_size=(
                    SCHEDULED_WATERMARK_FONT_SIZE
                    if use_fixed_erp_image_policy
                    else None
                ),
                # 定时任务的勾选状态覆盖租户全局水印开关；普通文章调用不传该
                # 参数，继续使用素材归档层已有的全局配置回退逻辑。
                watermark_enabled=bool(getattr(task, "enable_watermark", False)),
                # 非空时使用任务保存的水印快照，避免每次执行重新读取全局样式。
                task_watermark_config=getattr(task, "watermark_config", None),
            )
        )
        # 让 state 中的图片元数据同步使用归档版本，后续封面选择不能回退到临时 URL。
        if len(state.images) == len(normalized_images.body_image_urls):
            # 连续海报的三张切片共享一个上游主视觉 URL，不能使用普通字典映射，
            # 否则三个状态对象会全部指向同一张图。正文 DOM 顺序才是这里的稳定契约。
            for image, archived_url in zip(state.images, normalized_images.body_image_urls):
                image.url = archived_url
        else:
            for image in state.images:
                image.url = normalized_images.url_mapping.get(image.url, image.url)
        final_content = normalized_images.content
        state.content = final_content
        state.full_content = append_ai_image_disclaimer(final_content)

        # 5. 更新 Article
        title_text = state.title.main_title if state.title else (topic or "")
        article.topic = state.topic
        article.main_title = title_text
        article.content = state.full_content or state.content or ""
        article.full_content = state.full_content or state.content or ""
        # 持久化 Agent 的图片元数据，便于后台排查“已生成但未入正文”等问题；正文
        # 仍是发布的唯一真相，因此图片元数据只作为诊断和素材审计使用。
        article.images = [image.model_dump() for image in state.images if image.url]

        cover_image_url = _select_article_cover(state, article.full_content or "")
        if cover_image_url:
            article.cover_image = cover_image_url
            print(f"  🖼️ 封面: {cover_image_url[:60]}")

        # 内容生成完成不等于微信已接收。先落中间态，发布失败时文章会明确停留在
        # generated，而不会因为异常分支提交运行记录而被误标为 draft_saved。
        article.status = "generated"
        article.phase = "CONTENT_GENERATED"
        db.commit()

        print(f"  ✅ 内容生成完成: {title_text[:40]}")

        # 7. 发布到微信
        _publish_to_wechat(
            db,
            article,
            account_ids,
            publish_mode,
            task,
            run=run,
        )
        _finalize_article_delivery(db, article, publish_mode)
        generated_article_id = article.id

    db.commit()
    print(f"\n  ✅ 图文任务完成")
    return generated_article_id


async def _gen_images_from_references(state, ref_image_urls):
    """使用参考图片理解 + AI 生成配图"""
    import re as _re
    from app.agent.nodes.image_understanding_node import understand_images
    from app.agent.nodes.prompt_crafting_node import craft_prompt
    from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.image_generation_service import image_generation_service
    from app.services.reference_image_imitation_service import build_reference_image_prompt
    from app.services.reference_media_analysis_service import analyze_reference_images
    from app.schemas.article import ImageResult

    if not ref_image_urls:
        print(f"  ⚠️ 无参考图片，跳过 AI 配图")
        return

    print(f"  ▶ 理解参考图片（{len(ref_image_urls)} 张）...")
    try:
        analysis = await asyncio.to_thread(
            analyze_reference_images,
            ref_image_urls,
            understand_images,
        )
    except Exception as e:
        print(f"  ⚠️ 图片理解失败: {e}")
        return

    visual_descs = [image.description for image in analysis.usable_images]
    if analysis.skipped_qrcode_count:
        print(f"  🚫 已跳过 {analysis.skipped_qrcode_count} 张二维码参考图")
    if not visual_descs:
        print(f"  ⚠️ 图片理解未返回描述")
        return

    # 从正文中提取 [IMAGE:] 占位符
    placeholders = list(_re.finditer(r'\[IMAGE:position=(\d+),keywords=([^,\]]+),type=([^\]]+)\]', state.content or ""))
    if not placeholders:
        print(f"  ⚠️ 正文中无 [IMAGE:] 占位符")
        return

    print(f"  ▶ AI 生成配图（{len(placeholders)} 张）...")

    async def _gen_all():
        results = []
        for idx, m in enumerate(placeholders):
            pos = int(m.group(1))
            orig_kw = m.group(2)
            img_type = m.group(3)

            desc_idx = idx % len(visual_descs)
            desc = visual_descs[desc_idx]
            prompt = build_reference_image_prompt(
                desc,
                orig_kw,
                craft_prompt,
                build_wanxiang_prompt,
            )
            if not prompt:
                print(f"    ⚠️ 图片 {idx+1} 未生成提示词")
                results.append(
                    ImageResult(
                        position=pos,
                        url="",
                        method="WANXIANG_IMITATE",
                        keywords=orig_kw,
                        section_title="",
                        description=str(desc),
                        placeholder_id=m.group(0),
                    )
                )
                continue

            print(f"    >>> 图片 {idx+1}/{len(placeholders)} (pos={pos}) <<<")
            generated = await image_generation_service.generate(ImageGenerationRequest(
                prompt=prompt,
                size="1024*1365",
                tenant_id=state.tenant_id,
            ))
            img_url = generated.url
            method = (
                f"{generated.provider}-fallback"
                if generated.fallback_used
                else generated.provider
            )
            results.append(ImageResult(position=pos, url=img_url or "", method=method, keywords=orig_kw, section_title="", description=str(desc), placeholder_id=m.group(0)))
            if img_url:
                print(f"      ✅ 图片 {idx+1}")
            else:
                print(f"      ⚠️ 图片 {idx+1} 生成失败")
        return results

    state.images = await _gen_all()
    success = len([img for img in state.images if img.url])
    print(f"  ✅ AI 配图完成: {success}/{len(placeholders)} 张")


def _scheduled_image(
    db,
    task,
    topic,
    fallback_topic,
    account_ids,
    publish_mode,
    *,
    run: ScheduledTaskRun | None = None,
):
    """纯图片类型：和创建文章完全相同的图片生成流程。"""
    import asyncio
    import re as _re
    from functools import partial
    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.image_generation_service import image_generation_service
    from app.services.storage_service import storage_service
    from app.services.wechat_publisher import publish_article
    from app.models.mysql_models import Article

    print(f"\n  >>> 纯图片 <<<")

    # === 有投喂源：使用 Agent 仿写流程 ===
    ref = None
    task_feed_article_ids = task.feed_article_ids or []
    has_feed_source = task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id)

    if task_feed_article_ids:
        # 用户选了具体文章 — 直接取
        from app.models.mysql_models import FeedSourceArticle as FSA
        refs = db.query(FSA).filter(FSA.id.in_(task_feed_article_ids)).all()
        if refs:
            ref = refs[0]
            print(f"  [纯图片仿写] 选中文章: {ref.title}")
    elif has_feed_source:
        # 用户没选具体文章 — 从投喂源取最近有图的文章
        from app.models.mysql_models import FeedSourceArticle as FSA
        source_ids = task.feed_source_ids or ([task.feed_source_id] if task.feed_source_id else [])
        if source_ids:
            candidates = db.query(FSA).filter(
                FSA.feed_source_id.in_(source_ids),
                FSA.body_markdown.isnot(None),
            ).order_by(FSA.id.desc()).limit(5).all()
            for c in candidates:
                imgs = _re.findall(r'!\[.*?\]\((.*?)\)', c.body_markdown or "")
                if imgs:
                    ref = c
                    print(f"  [纯图片仿写] 取投喂源文章: {ref.title} ({len(imgs)} 张图)")
                    break

    if ref is not None:
        # Agent 仿写流程 — 视觉理解参考图片 → 生成新图
        try:
            from app.agent.nodes.title_imitation_node import imitate_title
            from app.agent.nodes.image_understanding_node import understand_images
            from app.agent.nodes.prompt_crafting_node import craft_prompt
            from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
            from app.services.reference_image_imitation_service import imitate_reference_images
            from app.services.reference_media_analysis_service import extract_markdown_image_urls

            ref_title = ref.title or ""
            ref_body = ref.body_markdown or ""
            image_urls_from_ref = extract_markdown_image_urls(ref_body)
            print(f"  提取图片: {len(image_urls_from_ref)} 张")

            if not image_urls_from_ref:
                print(f"  ⚠️ 参考文章中没有图片，跳过")
                return

            # Agent 1: 标题（用用户主题或仿写，不兜底任务名称）
            new_title = topic  # 用户没设主题时就是 None，走仿写
            if not new_title:
                titles = imitate_title(ref_title, topic="", count=3)
                new_title = titles[0] if titles else ref_title
            print(f"  标题: {new_title}")

            # 两个纯图片入口统一复用同一编排服务，二维码只跳过自身，其余图片仍按
            # 原始顺序仿写，避免定时任务与即时任务的处理结果不一致。
            imitation_result = asyncio.run(
                imitate_reference_images(
                    image_urls_from_ref,
                    new_title,
                    tenant_id=task.tenant_id,
                    understand_images_fn=understand_images,
                    craft_prompt_fn=craft_prompt,
                    fallback_prompt_fn=build_wanxiang_prompt,
                    # 仿写服务维持供应商无关的回调边界；partial 固定租户，确保中转站
                    # 返回的 Base64 图片进入正确租户的 MinIO 目录。
                    generate_image_fn=partial(
                        image_generation_service.generate_image,
                        tenant_id=task.tenant_id,
                    ),
                    archive_image_fn=lambda tenant_id, image_url, **kwargs: save_image_to_asset_library(
                        db,
                        tenant_id,
                        image_url,
                        **kwargs,
                    ),
                )
            )
            image_urls = list(imitation_result.generated_urls)
            if imitation_result.skipped_qrcode_count:
                print(f"  🚫 已跳过 {imitation_result.skipped_qrcode_count} 张二维码参考图")

            if not image_urls:
                if imitation_result.skipped_qrcode_count == len(image_urls_from_ref):
                    print(f"  ⚠️ 所有参考图片均为二维码，已跳过仿写")
                else:
                    print(f"  ❌ 所有非二维码图片生成失败")
                return

            from app.services.article_publication_polish_service import (
                append_ai_image_disclaimer,
                archive_image_urls_with_attribution,
            )

            image_urls = asyncio.run(
                archive_image_urls_with_attribution(
                    db,
                    image_urls,
                    tenant_id=task.tenant_id,
                    product_name=new_title,
                )
            )

            body_md = append_ai_image_disclaimer(
                "\n\n".join(f"![]({url})" for url in image_urls)
            )

            article = Article(
                task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
                tenant_id=task.tenant_id,
                main_title=new_title,
                content=body_md,
                full_content=body_md,
                cover_image=image_urls[0],
                status="generated",
                phase="CONTENT_GENERATED",
            )
            _persist_scheduled_article(db, article, run_id=getattr(run, "id", None))

            _publish_to_wechat(
                db,
                article,
                account_ids,
                publish_mode,
                task,
                run=run,
            )
            _finalize_article_delivery(db, article, publish_mode)
            print(f"  ✅ 纯图片仿写完成: {len(image_urls)} 张图")
            return article.id

        except Exception as e:
            print(f"  ❌ 仿写流程失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    # === 无投喂源：通用图片生成（3 张） ===
    # 有主题用主题，无主题就不生成（不兜底任务名称）
    if not topic:
        print(f"  ⚠️ 无主题且无投喂源，跳过纯图片生成")
        return
    async def _run():
        prompts = [
            f"{topic}，宽景构图，柔和自然光线，干净留白背景，专业摄影，高级质感。不要包含任何文字或文本标签，纯图像。",
            f"{topic}，细节特写，质感丰富，浅景深，柔和光影。不要包含任何文字或文本标签，纯图像。",
            f"{topic}，场景氛围，自然光线，干净构图，温暖色调。不要包含任何文字或文本标签，纯图像。",
        ]

        image_urls = []
        image_keys = []

        for i in range(3):
            print(f"  ▶ 生成图片 {i+1}/3...")
            try:
                img_url = await image_generation_service.generate_image(
                    prompts[i],
                    size="1024*1365",
                    tenant_id=task.tenant_id,
                )
                if img_url:
                    asset = await save_image_to_asset_library(
                        db, task.tenant_id, img_url,
                        keywords=topic[:50], usage_type="generated_image",
                    )
                    image_urls.append(img_url)
                    if asset:
                        image_keys.append(asset.storage_key)
                    print(f"    ✅ 图片 {i+1}")
                else:
                    print(f"    ⚠️ 图片 {i+1} 为空")
            except Exception as e:
                print(f"    ⚠️ 图片 {i+1} 失败: {e}")

        return image_urls, image_keys

    image_urls, image_keys = asyncio.run(_run())

    if not image_urls:
        print(f"  ❌ 所有图片生成失败")
        return

    from app.services.article_publication_polish_service import (
        append_ai_image_disclaimer,
        archive_image_urls_with_attribution,
    )

    image_urls = asyncio.run(
        archive_image_urls_with_attribution(
            db,
            image_urls,
            tenant_id=task.tenant_id,
            product_name=topic,
        )
    )

    body_md = append_ai_image_disclaimer(
        "\n\n".join(f"![]({url})" for url in image_urls)
    )

    article = Article(
        task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
        tenant_id=task.tenant_id,
        main_title=topic,
        content=body_md,
        full_content=body_md,
        cover_image=image_urls[0],
        status="generated",
        phase="CONTENT_GENERATED",
    )
    _persist_scheduled_article(db, article, run_id=getattr(run, "id", None))

    _publish_to_wechat(
        db,
        article,
        account_ids,
        publish_mode,
        task,
        run=run,
    )
    _finalize_article_delivery(db, article, publish_mode)
    print(f"  ✅ 纯图片完成: {len(image_urls)} 张图")
    return article.id


def _scheduled_video(
    db,
    task,
    topic,
    fallback_topic,
    account_ids,
    publish_mode,
    *,
    run: ScheduledTaskRun | None = None,
):
    """视频类型：和创建文章完全相同的视频生成流程。"""
    import asyncio
    import uuid as _uuid
    from app.models.mysql_models import Article
    from app.services.storage_service import generate_object_key as _gen_key, storage_service as _ss
    from app.services.wechat_publisher import publish_article
    from app.services.video_gen_service import video_gen_service as _vgen
    from app.config import settings

    use_topic = topic or fallback_topic
    print(f"\n  >>> 视频 <<<")
    print(f"  [视频] 开始处理: {use_topic}")

    async def _run():
        dur = settings.video_duration_sec if hasattr(settings, 'video_duration_sec') else 5
        ar = "9:16"
        size = "720*1280"
        prompt = use_topic

        print(f"  >>> 提交文生视频: {prompt[:80]}")
        video_url = await _vgen.generate_video(prompt=prompt, size=size, duration=dur)
        if not video_url:
            raise RuntimeError("视频生成失败，请检查 API Key 是否有万相视频模型权限")

        print(f"  ✅ 视频生成完毕")

        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=120) as _client:
            resp = await _client.get(video_url)
            resp.raise_for_status()
            video_bytes = resp.content

        vk = _gen_key(task.tenant_id, f"video_{_uuid.uuid4().hex[:8]}.mp4", prefix="content")
        _ss.upload_bytes(vk, video_bytes, "video/mp4")
        vu = _ss.get_url(vk)
        print(f"  ✅ 视频已保存")

        # 生成封面
        cover_url = ""
        try:
            from app.services.image_generation_service import image_generation_service
            cover_prompt = f"{use_topic}，封面图，视觉冲击力，高清，适合做视频封面"
            _cover_img_url = await image_generation_service.generate_image(
                cover_prompt,
                size="720*1280",
                tenant_id=task.tenant_id,
            )
            if _cover_img_url:
                from app.services.asset_archive_service import save_image_to_asset_library
                _asset = await save_image_to_asset_library(
                    db, task.tenant_id, _cover_img_url, keywords=f"video_cover",
                )
                if _asset and _asset.storage_key:
                    cover_url = _ss.get_url(_asset.storage_key)
                    print(f"  ✅ 封面已生成")
        except Exception as e:
            print(f"  ⚠️ 封面生成异常: {e}")

        return vu, cover_url

    video_url, cover_url = asyncio.run(_run())

    article = Article(
        task_id=f"sched_vid_{task.id}_{_uuid.uuid4().hex[:6]}",
        tenant_id=task.tenant_id,
        main_title=use_topic,
        content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        full_content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        cover_image=cover_url,
        status="generated",
        phase="CONTENT_GENERATED",
    )
    _persist_scheduled_article(db, article, run_id=getattr(run, "id", None))

    _publish_to_wechat(
        db,
        article,
        account_ids,
        publish_mode,
        task,
        run=run,
    )
    _finalize_article_delivery(db, article, publish_mode)
    print(f"  ✅ 视频完成")
    return article.id


def _publish_to_wechat(
    db,
    article,
    account_ids,
    publish_mode,
    task,
    *,
    run: ScheduledTaskRun | None = None,
):
    """逐个调用公众号发布接口，并持久化每个账号的交付结果。

    本方法只负责外部交付，不修改文章最终状态；调用方必须在本方法完整返回后调用
    ``_finalize_article_delivery``。账号级结果先提交再处理下一个账号，Worker 在
    多账号中途失败时，下一次重试只会补发未完成账号，不会重新提交已成功文章。
    """
    if publish_mode not in {"draft", "direct"}:
        # 必须在导入和调用发布服务前校验，避免错误配置先触发真实微信请求。
        raise ValueError(f"不支持的定时任务发布模式：{publish_mode}")
    if not account_ids:
        raise ValueError("定时任务未配置公众号，无法完成发布")

    from app.services.wechat_publisher import WechatPublishAmbiguousError, publish_article
    from app.services.wechat_relay_client import WechatRelayPublishAmbiguousError

    publish_domain = resolve_scheduled_publish_domain(task, run)
    delivery_results = dict(getattr(run, "delivery_results", None) or {}) if run else {}
    pending_account_ids = [
        aid
        for aid in account_ids
        if not _is_successful_scheduled_delivery(
            delivery_results,
            article_id=article.id,
            account_id=aid,
            publish_mode=publish_mode,
            publish_domain=publish_domain,
        )
    ]

    # 已经存在“请求发出但结果不明确”的记录时，自动重试不能再次调用微信。
    # 该状态只允许人工核对公众号后台后处理，优先保护“不重复发布”这一外部约束。
    for aid in pending_account_ids:
        previous_result = delivery_results.get(_scheduled_delivery_key(article.id, aid))
        if (
            isinstance(previous_result, dict)
            and previous_result.get("mode") == publish_mode
            and (
                previous_result.get("publish_domain") is None
                or normalize_publish_domain(previous_result.get("publish_domain"))
                == publish_domain
            )
            and previous_result.get("status") in {"partial", "ambiguous"}
        ):
            raise RuntimeError(
                f"公众号 #{aid} 已存在未确认的微信交付结果，禁止自动重复发布；请先人工核对"
            )

    # 草稿保存不包含正式发布的二阶段状态，也不会修改同一个公众号的外部文章；
    # 因而不同账号可以有限并发。正式发布仍严格串行，避免并发改变微信发布顺序
    # 或把部分成功语义变得不可解释。每个子工作单元自行创建数据库会话，主会话
    # 绝不能跨线程传递给 SQLAlchemy 或微信发布服务。
    if publish_mode == "draft" and run is not None and len(pending_account_ids) > 1:
        from app.services.scheduled_delivery_service import execute_bounded_draft_deliveries

        executions = execute_bounded_draft_deliveries(
            pending_account_ids,
            max_workers=getattr(settings, "scheduled_draft_delivery_max_workers", 2),
            deliver=lambda account_id: _publish_scheduled_draft_for_account(
                article_id=article.id,
                task_id=task.id,
                run_id=run.id,
                account_id=account_id,
                publish_domain=publish_domain,
            ),
        )
        failed_execution = next(
            (execution for execution in executions if execution.error is not None),
            None,
        )
        if failed_execution is not None:
            raise RuntimeError(
                f"发布到公众号 #{failed_execution.account_id} 失败: {failed_execution.error}"
            ) from failed_execution.error
        return

    for aid in pending_account_ids:
        delivery_key = _scheduled_delivery_key(article.id, aid)
        try:
            result = publish_article(
                db,
                article,
                aid,
                mode=publish_mode,
                publish_domain=publish_domain,
                tenant_id=task.tenant_id,
                actor_id=task.created_by or 0,
            )
            if not isinstance(result, dict):
                raise RuntimeError("公众号发布接口没有返回结构化结果")

            # 直接发布接口可能已经把草稿写入微信，但正式发布请求失败；这种
            # “部分成功”必须先记录草稿 ID，再上抛给定时任务，禁止盲目重投。
            if result.get("publish_error"):
                partial_result = {
                    "status": "partial",
                    "mode": publish_mode,
                    "publish_domain": publish_domain,
                    "media_id": str(result.get("media_id") or "").strip(),
                    "error": str(result["publish_error"])[:2000],
                }
                if run is not None:
                    delivery_results[delivery_key] = partial_result
                    run.delivery_results = dict(delivery_results)
                    db.commit()
                raise RuntimeError(f"正式发布失败：{result['publish_error']}")

            delivery_result = {
                "status": "success",
                "mode": publish_mode,
                "publish_domain": publish_domain,
            }
            if publish_mode == "direct":
                if publish_domain == "private":
                    msg_id = str(
                        result.get("msg_id")
                        or result.get("msg_data_id")
                        or ""
                    ).strip()
                    if not msg_id:
                        raise RuntimeError("私域群发失败：微信未返回 msg_id")
                else:
                    publish_id = str(result.get("publish_id") or "").strip()
                    if not publish_id:
                        raise RuntimeError("公域发布失败：微信未返回 publish_id")
                    article.publish_id = publish_id
                    delivery_result["publish_id"] = publish_id
            else:
                media_id = str(result.get("media_id") or "").strip()
                if not media_id:
                    raise RuntimeError("保存草稿失败：微信未返回 media_id")
                delivery_result["media_id"] = media_id

            # 这些字段与账号级结果一起提交。若后续账号失败，已成功账号的标识
            # 和完成状态仍会保留下来，下一次执行可以安全跳过该账号。
            msg_data_id = str(result.get("msg_data_id") or result.get("msg_id") or "").strip()
            if msg_data_id:
                article.msg_data_id = msg_data_id
                delivery_result["msg_data_id"] = msg_data_id
            article.publish_domain = publish_domain
            article.wechat_account_id = aid
            if run is not None:
                delivery_results[delivery_key] = delivery_result
                run.delivery_results = dict(delivery_results)
                db.commit()
            logger.info(
                "已%s到公众号 #%s",
                (
                    "私域群发"
                    if publish_mode == "direct" and publish_domain == "private"
                    else "公域发布"
                    if publish_mode == "direct"
                    else "保存草稿"
                ),
                aid,
            )
        except Exception as e:
            ambiguous_publish_error_types = tuple(
                error_type
                for error_type in (
                    WechatPublishAmbiguousError,
                    WechatRelayPublishAmbiguousError,
                )
                if isinstance(error_type, type)
            )
            if run is not None and any(
                isinstance(item, ambiguous_publish_error_types)
                for item in _iter_exception_chain(e)
            ):
                delivery_results[delivery_key] = {
                    "status": "ambiguous",
                    "mode": publish_mode,
                    "publish_domain": publish_domain,
                    "error": str(e)[:2000],
                }
                run.delivery_results = dict(delivery_results)
                db.commit()
            logger.error("发布到公众号 #%s 失败: %s", aid, e)
            raise RuntimeError(f"发布到公众号 #{aid} 失败: {e}") from e


def _persist_scheduled_draft_delivery_result(
    db,
    *,
    run_id: int,
    article_id: int,
    account_id: int,
    publish_domain: str,
    result: dict,
) -> bool:
    """在账号工作单元结束时锁定运行记录并保存单个草稿结果。

    并发账号不能先读取同一份 JSON 后再各自覆盖写回。这里在最终写入点对运行记录
    加行锁、重新读取当前 ``delivery_results``，让每个账号只增量合并自己的键；
    同时再检查是否已经成功，防御 Celery 重投或异常恢复和当前工作单元交错。
    """

    locked_run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.id == run_id)
        .with_for_update()
        .first()
    )
    if locked_run is None:
        raise RuntimeError(f"定时运行 #{run_id} 不存在，已取消草稿结果写入")
    delivery_results = dict(locked_run.delivery_results or {})
    if _is_successful_scheduled_delivery(
        delivery_results,
        article_id=article_id,
        account_id=account_id,
        publish_mode="draft",
        publish_domain=publish_domain,
    ):
        db.rollback()
        return False
    delivery_results[_scheduled_delivery_key(article_id, account_id)] = dict(result)
    locked_run.delivery_results = delivery_results
    db.commit()
    return True


def _publish_scheduled_draft_for_account(
    *,
    article_id: int,
    task_id: int,
    run_id: int,
    account_id: int,
    publish_domain: str,
) -> dict:
    """在独立会话中向一个公众号保存草稿并立即记录幂等结果。

    这个函数是多账号草稿并发的事务边界：只加载本账号所需对象、调用一次外部接口、
    并持久化一条账号级结果。出现不明确响应时先写 ``ambiguous``，后续自动重试会
    被原有保护逻辑阻止，不能因为并发而重复创建微信草稿。
    """

    from app.models.mysql_models import Article
    from app.services.wechat_publisher import WechatPublishAmbiguousError, publish_article

    db = MysqlSessionLocal()
    delivery_key = _scheduled_delivery_key(article_id, account_id)
    normalized_domain = normalize_publish_domain(publish_domain)
    try:
        article = db.query(Article).filter(Article.id == article_id).first()
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        run = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()
        if article is None or task is None or run is None:
            raise RuntimeError("草稿投递所需的文章、任务或运行记录不存在")

        delivery_results = dict(run.delivery_results or {})
        if _is_successful_scheduled_delivery(
            delivery_results,
            article_id=article_id,
            account_id=account_id,
            publish_mode="draft",
            publish_domain=normalized_domain,
        ):
            return {"status": "skipped"}
        previous_result = delivery_results.get(delivery_key)
        if (
            isinstance(previous_result, dict)
            and previous_result.get("mode") == "draft"
            and normalize_publish_domain(previous_result.get("publish_domain")) == normalized_domain
            and previous_result.get("status") in {"partial", "ambiguous"}
        ):
            raise RuntimeError(
                f"公众号 #{account_id} 已存在未确认的微信交付结果，禁止自动重复发布；请先人工核对"
            )

        result = publish_article(
            db,
            article,
            account_id,
            mode="draft",
            publish_domain=normalized_domain,
            tenant_id=task.tenant_id,
            actor_id=task.created_by or 0,
        )
        if not isinstance(result, dict):
            raise RuntimeError("公众号发布接口没有返回结构化结果")
        if result.get("publish_error"):
            partial_result = {
                "status": "partial",
                "mode": "draft",
                "publish_domain": normalized_domain,
                "media_id": str(result.get("media_id") or "").strip(),
                "error": str(result["publish_error"])[:2000],
            }
            _persist_scheduled_draft_delivery_result(
                db,
                run_id=run_id,
                article_id=article_id,
                account_id=account_id,
                publish_domain=normalized_domain,
                result=partial_result,
            )
            raise RuntimeError(f"保存草稿失败：{result['publish_error']}")
        media_id = str(result.get("media_id") or "").strip()
        if not media_id:
            raise RuntimeError("保存草稿失败：微信未返回 media_id")
        delivery_result = {
            "status": "success",
            "mode": "draft",
            "publish_domain": normalized_domain,
            "media_id": media_id,
        }
        _persist_scheduled_draft_delivery_result(
            db,
            run_id=run_id,
            article_id=article_id,
            account_id=account_id,
            publish_domain=normalized_domain,
            result=delivery_result,
        )
        logger.info("已保存草稿到公众号 #%s", account_id)
        return delivery_result
    except Exception as exc:
        db.rollback()
        if any(
            isinstance(error, WechatPublishAmbiguousError)
            for error in _iter_exception_chain(exc)
        ):
            ambiguous_result = {
                "status": "ambiguous",
                "mode": "draft",
                "publish_domain": normalized_domain,
                "error": str(exc)[:2000],
            }
            try:
                _persist_scheduled_draft_delivery_result(
                    db,
                    run_id=run_id,
                    article_id=article_id,
                    account_id=account_id,
                    publish_domain=normalized_domain,
                    result=ambiguous_result,
                )
            except Exception as persist_error:
                db.rollback()
                logger.error("公众号 #%s 的不明确草稿结果未能保存: %s", account_id, persist_error)
        logger.error("发布草稿到公众号 #%s 失败: %s", account_id, exc)
        raise
    finally:
        db.close()


def _finalize_article_delivery(db, article, publish_mode: str) -> None:
    """在微信交付真实成功后统一写入文章最终状态并提交事务。

    图文、纯图片和视频共享该收口点，避免各流程自行维护状态字符串而再次出现
    “尚未保存微信草稿却已标记成功”的时序错误。
    """
    if publish_mode == "direct":
        article.status = "published"
        article.phase = "PUBLISHED"
    else:
        article.status = "draft_saved"
        article.phase = "DRAFT_SAVED"
    db.commit()


def _load_layout_template(state, feed_article) -> None:
    """加载并净化投喂源版式模板，禁止联系方式章节进入正文 Agent。"""
    from app.schemas.article import LayoutTemplate
    from app.services.reference_contact_filter_service import (
        remove_contact_sections_from_layout_template,
    )

    if not feed_article or not feed_article.analysis:
        return

    analysis = feed_article.analysis
    if not isinstance(analysis, dict):
        return

    if analysis.get("layout_status") != "completed":
        return

    template_data = analysis.get("layout_template")
    if not template_data:
        return

    try:
        state.layout_template = remove_contact_sections_from_layout_template(
            LayoutTemplate(**template_data)
        )
        section_count = len(state.layout_template.sections)
        print(f"  Template loaded: {section_count} sections, {state.layout_template.total_image_count} images")
    except Exception as exc:
        print(f"  Template parse failed: {exc}")


def _build_reference_article_for_imitation(title: str, markdown: str) -> str:
    """构建安全的投喂源文字上下文，彻底移除来源账号的末尾联系区。"""

    from app.services.reference_contact_filter_service import strip_reference_contact_markdown

    cleaned_markdown = strip_reference_contact_markdown(markdown)
    if not cleaned_markdown:
        return ""
    return f"## {title}\n\n{cleaned_markdown}"
