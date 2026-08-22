"""TaGeAI Integration 状态映射测试。

这些测试不依赖 MySQL 或微信开放平台，而是锁定 Gateway 对外可见状态必须来自真实
ContentJob/Article 事实，不能再由内存线程伪造 DRAFT_SAVED 或 PUBLISHED。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.integrations.tageai.service import derive_invocation_state


@pytest.fixture(autouse=True)
def reset_test_tables():
    """状态映射是纯函数，不能因无关数据库夹具阻止回归验证。"""

    yield


def test_queued_content_job_maps_to_queued_invocation_state():
    job = SimpleNamespace(status="queued", error_code=None, error_message=None)

    state = derive_invocation_state(job, None, "DRAFT")

    assert state["status"] == "QUEUED"
    assert state["phase"] == "QUEUED"
    assert state["progress"] == 0


def test_generating_job_projects_platform_level_media_progress_snapshot():
    """媒体子任务进度应聚合为一条公众号进度，并随 Invocation 对外返回估时。"""

    job = SimpleNamespace(
        status="generating",
        error_code=None,
        error_message=None,
        generation_config={
            "progress_snapshot": {
                "platform": "wechat",
                "stage": "MEDIA_GENERATING",
                "text_progress": 100,
                "media_total": 5,
                "media_ready": 3,
                "media_generating": 1,
            }
        },
    )

    state = derive_invocation_state(job, None, "PREVIEW")

    assert state["status"] == "MEDIA_GENERATING"
    assert state["phase"] == "MEDIA_GENERATING"
    assert state["progress"] == 70
    assert state["platform"] == "wechat"
    assert state["platform_label"] == "微信公众号"
    assert state["media_summary"]["ready"] == 3
    assert state["estimated_remaining_seconds"]["min"] > 0


def test_stale_generating_job_without_heartbeat_converges_to_retryable_failure(monkeypatch):
    """旧 Worker 遗留的生成任务必须落成明确失败，而不是无限停在 GENERATING。"""

    from app.integrations.tageai import service as tageai_service
    from app.config import settings
    from app.models.mysql_models import ContentJob, TageAiIntegrationInvocation

    stale_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    invocation = SimpleNamespace(
        id=6001,
        invocation_id="invocation-stale-generation",
        tenant_id=7,
        content_job_id=901,
        tenant_binding_id="tenant-binding-7",
        delivery_mode="PREVIEW",
        external_job_id="content-job-901",
        status="GENERATING",
        phase="GENERATING",
        progress=30,
        result_data=None,
        error_code=None,
        error_message=None,
        retryable=False,
        created_at=stale_time,
        started_at=stale_time,
        finished_at=None,
    )
    job = SimpleNamespace(
        id=901,
        tenant_id=7,
        status="generating",
        error_code=None,
        error_message=None,
        generation_config={},
        updated_at=stale_time,
    )
    db = _InvocationStateDb({
        TageAiIntegrationInvocation: [invocation],
        ContentJob: [job],
    }, callback_exists=True)
    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", lambda: db)
    # 用例验证的是超时收敛，不应依赖开发机的默认超时配置。
    monkeypatch.setattr(settings, "tageai_generation_heartbeat_timeout_seconds", 60)

    state = tageai_service.get_invocation("invocation-stale-generation", 7)

    assert state["status"] == "FAILED"
    assert state["phase"] == "GENERATION_WORKER_STALE"
    assert state["error_code"] == "GENERATION_WORKER_STALE"
    assert state["retryable"] is True
    assert job.status == "failed"
    assert job.error_code == "GENERATION_WORKER_STALE"
    assert invocation.status == "FAILED"
    assert db.commit_count == 1


def test_recent_generating_job_with_heartbeat_remains_running(monkeypatch):
    """仍在刷新心跳的生成任务不能被恢复逻辑误判为失活。"""

    from app.integrations.tageai import service as tageai_service
    from app.models.mysql_models import ContentJob, TageAiIntegrationInvocation

    now = datetime.now(timezone.utc)
    invocation = SimpleNamespace(
        id=6002,
        invocation_id="invocation-live-generation",
        tenant_id=7,
        content_job_id=902,
        tenant_binding_id="tenant-binding-7",
        delivery_mode="PREVIEW",
        external_job_id="content-job-902",
        status="GENERATING",
        phase="GENERATING",
        progress=30,
        result_data=None,
        error_code=None,
        error_message=None,
        retryable=False,
        created_at=now,
        started_at=now,
        finished_at=None,
    )
    job = SimpleNamespace(
        id=902,
        tenant_id=7,
        status="generating",
        error_code=None,
        error_message=None,
        generation_config={
            "progress_snapshot": {
                "stage": "TEXT_GENERATING",
                "text_progress": 25,
                "heartbeat_at": now.isoformat(),
            },
        },
        updated_at=now,
    )
    db = _InvocationStateDb({
        TageAiIntegrationInvocation: [invocation],
        ContentJob: [job],
    }, callback_exists=True)
    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", lambda: db)

    state = tageai_service.get_invocation("invocation-live-generation", 7)

    assert state["status"] == "TEXT_GENERATING"
    assert job.status == "generating"
    assert invocation.status == "GENERATING"
    assert db.commit_count == 0


def test_saved_draft_article_maps_to_draft_saved_with_result_reference():
    job = SimpleNamespace(status="approved", error_code=None, error_message=None)
    article = SimpleNamespace(
        id=42,
        status="draft_saved",
        phase="DRAFT_SAVED",
        main_title="可交付的文章",
        publish_id="draft-media-id",
        msg_data_id=None,
        error_message=None,
    )

    attempt = SimpleNamespace(status="success", error_code=None, error_message=None)

    state = derive_invocation_state(job, article, "DRAFT", attempt)

    assert state["status"] == "DRAFT_SAVED"
    assert state["result"]["draftId"] == "draft-media-id"
    assert state["result"]["contentRef"] == "wechat://articles/42"


def test_generated_article_result_exposes_a_read_only_preview_artifact():
    """已生成正文必须随状态结果返回，供桌面端在真实工件到达后再打开预览。

    ``contentRef`` 只是平台内部文章的定位符，Renderer 无权凭它读取任意资源；若不在
    受控结果中显式投影正文，桌面端会永久停留在“正在生成文章”。测试同时固定预览只
    包含展示所需字段，避免把发布凭据或内部账号标识带出服务边界。
    """

    job = SimpleNamespace(status="approved", error_code=None, error_message=None)
    article = SimpleNamespace(
        id=43,
        status="draft_saved",
        phase="DRAFT_SAVED",
        topic="科技文章",
        main_title="可预览的科技文章",
        content="这是一段已经生成完成的公众号正文。",
        cover_image="https://cdn.example.com/covers/technology.png",
        publish_id="draft-media-id-43",
        msg_data_id=None,
        error_message=None,
    )
    attempt = SimpleNamespace(status="success", error_code=None, error_message=None)

    state = derive_invocation_state(job, article, "DRAFT", attempt)

    assert state["result"]["articlePreview"] == {
        "title": "可预览的科技文章",
        "content": "这是一段已经生成完成的公众号正文。",
        "coverImageUrl": "https://cdn.example.com/covers/technology.png",
    }


def test_publishing_article_maps_to_publish_progress_without_claiming_completion():
    job = SimpleNamespace(status="publishing", error_code=None, error_message=None)
    article = SimpleNamespace(
        id=42,
        status="publishing",
        phase="PUBLISHING",
        main_title="待发布文章",
        publish_id="publish-001",
        msg_data_id=None,
        error_message=None,
    )

    attempt = SimpleNamespace(status="publishing", error_code=None, error_message=None)

    state = derive_invocation_state(job, article, "PUBLISH", attempt)

    assert state["status"] == "PUBLISHING"
    assert state["phase"] == "PUBLISHING"
    assert state["progress"] == 90


def test_failed_draft_attempt_overrides_stale_draft_saved_article_status():
    """真实投递失败时，TaGeAI 查询不得把历史文章字段误报成草稿成功。"""

    job = SimpleNamespace(status="approved", error_code=None, error_message=None)
    article = SimpleNamespace(
        id=42,
        status="draft_saved",
        phase="DRAFT_SAVED",
        main_title="投递失败的文章",
        publish_id=None,
        msg_data_id=None,
        error_message="草稿箱不可用",
    )
    attempt = SimpleNamespace(
        status="failed",
        error_code="DRAFT_DELIVERY_FAILED",
        error_message="草稿箱不可用",
    )

    state = derive_invocation_state(job, article, "DRAFT", attempt)

    assert state["status"] == "FAILED"
    assert state["error_code"] == "DRAFT_DELIVERY_FAILED"


def test_unknown_relay_article_without_attempt_keeps_status_unavailable_diagnostic():
    """投递记录缺失时也必须保留 relay 状态未知诊断，而不是误报发布失败。

    PublishAttempt 通常优先，但历史数据、人工清理或部分事务失败可能只留下文章记录；
    此时不能把 relay 状态查询超时错误映射成草稿保存失败。
    """

    job = SimpleNamespace(status="approved", error_code=None, error_message=None)
    article = SimpleNamespace(
        id=42,
        status="unknown",
        phase="PUBLISH_STATUS_UNKNOWN",
        main_title="超时文章",
        publish_id="relay-publish-001",
        msg_data_id=None,
        error_message="中转站没有最终状态查询能力",
    )

    state = derive_invocation_state(job, article, "PUBLISH")

    assert state["status"] == "UNKNOWN"
    assert state["phase"] == "PUBLISH_STATUS_UNKNOWN"
    assert state["error_code"] == "PUBLISH_STATUS_UNKNOWN"


class _InvocationStateQuery:
    """为 Integration 查询链路提供最小 SQLAlchemy Query 替身。

    本测试只验证状态收敛，不应连接真实 MySQL；查询条件由生产代码构造，替身保留
    ``filter`` 和 ``order_by`` 的链式接口，以便覆盖真实的读取顺序。
    """

    def __init__(self, rows):
        """保存指定模型对应的稳定结果集，避免测试依赖数据库夹具。"""

        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        """兼容生产查询条件；测试数据已按租户和调用关系预先隔离。"""

        return self

    def order_by(self, *args, **kwargs):
        """兼容最新版本和最近投递记录的排序调用。"""

        return self

    def first(self):
        """返回查询链路要求的首条记录。"""

        return self._rows[0] if self._rows else None

    def all(self):
        """返回完整投递集合，使超时收敛能同步更新全部未终态尝试。"""

        return list(self._rows)


class _InvocationStateDb:
    """记录查询时状态收敛写入的轻量数据库替身。

    超时失败必须由查询路径持久化，因此除读取结果外还显式记录 commit 次数，防止
    实现退化为仅在 HTTP 响应中临时伪造 FAILED。
    """

    def __init__(self, rows_by_model, *, callback_exists=False):
        """按 ORM 模型保存测试行，保持生产代码的查询边界不变。"""

        self._rows_by_model = rows_by_model
        self._callback_exists = callback_exists
        self.added = []
        self.commit_count = 0

    def query(self, model_class):
        """返回指定模型的查询替身，未声明的模型直接视为空结果。"""

        from app.models.mysql_models import TageAiIntegrationCallbackOutbox

        if model_class is TageAiIntegrationCallbackOutbox.id:
            # 近期状态查询已有同一快照，避免把普通 GET 误判成一次新的持久化写入。
            rows = [SimpleNamespace(id=3001)] if self._callback_exists else []
            return _InvocationStateQuery(rows)
        return _InvocationStateQuery(self._rows_by_model.get(model_class, []))

    def begin_nested(self):
        """提供超时状态首次写入 callback outbox 所需的嵌套事务上下文。"""

        return self

    def __enter__(self):
        """使数据库替身兼容 SQLAlchemy 嵌套事务的上下文协议。"""

        return self

    def __exit__(self, *_args):
        """不吞掉 outbox 插入异常，保持生产代码的失败传播语义。"""

        return False

    def add(self, value):
        """记录待写入的 callback outbox，便于调试测试事务行为。"""

        self.added.append(value)

    def flush(self):
        """模拟 ORM flush；本测试不依赖数据库生成的 outbox 主键。"""

        return None

    def commit(self):
        """记录持久化动作，验证终态不会只停留在内存对象中。"""

        self.commit_count += 1

    def close(self):
        """兼容服务层 finally 中的会话释放。"""

        return None


def _relay_publishing_query_context(submission_time, *, callback_exists=False):
    """构造 relay 已受理但尚无最终结果的完整 Integration 查询上下文。

    该夹具同时包含 Invocation、内容任务、版本、文章和投递记录，避免多个边界测试
    各自复制 ORM 关联。调用方只改变提交时间，即可验证超时与未超时两条分支。
    """

    from app.models.mysql_models import (
        Article,
        ContentJob,
        ContentVersion,
        PublishAttempt,
        TageAiIntegrationInvocation,
    )

    invocation = SimpleNamespace(
        id=5001,
        invocation_id="invocation-relay-timeout",
        tenant_id=7,
        content_job_id=901,
        tenant_binding_id="tenant-binding-7",
        delivery_mode="PUBLISH",
        external_job_id="content-job-901",
        status="QUEUED",
        phase="QUEUED",
        progress=0,
        result_data=None,
        error_code=None,
        error_message=None,
        retryable=False,
        created_at=submission_time,
        started_at=submission_time,
        finished_at=None,
    )
    job = SimpleNamespace(id=901, tenant_id=7, status="approved", error_code=None, error_message=None)
    version = SimpleNamespace(id=1001, tenant_id=7, job_id=901, article_id=42)
    article = SimpleNamespace(
        id=42,
        tenant_id=7,
        topic="状态收敛测试",
        main_title="状态收敛测试",
        status="publishing",
        phase="RELAY_PUBLISHING",
        publish_id="relay-publish-001",
        msg_data_id=None,
        error_message=None,
        created_at=submission_time,
        updated_at=submission_time,
        wechat_publish_time=submission_time,
    )
    attempt = SimpleNamespace(
        id=2001,
        tenant_id=7,
        job_id=901,
        status="publishing",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    db = _InvocationStateDb({
        TageAiIntegrationInvocation: [invocation],
        ContentJob: [job],
        ContentVersion: [version],
        Article: [article],
        PublishAttempt: [attempt],
    }, callback_exists=callback_exists)
    return db, invocation, article, attempt


def test_query_stale_relay_publish_converges_to_persistent_unknown(monkeypatch):
    """超过状态查询时限的 relay 发布必须持久化为可恢复的未知状态。

    Celery 轮询用于主动补偿，但外部调用方不能因 Beat 延迟而无限读取 PUBLISHING；
    因此 GET 查询发现 relay 没有最终状态协议且已超时后，必须同时收敛文章、投递
    尝试与 Invocation 的诊断字段。
    """

    from app.config import settings
    from app.integrations.tageai import service as tageai_service

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    db, invocation, article, attempt = _relay_publishing_query_context(stale_time)
    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(settings, "wechat_relay_publish_status_timeout_seconds", 60)

    state = tageai_service.get_invocation("invocation-relay-timeout", 7)

    assert state["status"] == "UNKNOWN"
    assert state["phase"] == "PUBLISH_STATUS_UNKNOWN"
    assert state["error_code"] == "PUBLISH_STATUS_UNKNOWN"
    assert state["finished_at"] is None
    assert article.status == "unknown"
    assert attempt.status == "unknown"
    assert invocation.status == "UNKNOWN"
    assert invocation.finished_at is None
    assert db.commit_count == 1


def test_query_recent_relay_publish_keeps_publishing_without_database_write(monkeypatch):
    """时限内的 relay 发布继续返回 PUBLISHING，不能被查询路径提前终态化。"""

    from app.config import settings
    from app.integrations.tageai import service as tageai_service

    submitted_at = datetime.now(timezone.utc)
    db, invocation, article, attempt = _relay_publishing_query_context(
        submitted_at,
        callback_exists=True,
    )
    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(settings, "wechat_relay_publish_status_timeout_seconds", 60)

    state = tageai_service.get_invocation("invocation-relay-timeout", 7)

    assert state["status"] == "PUBLISHING"
    assert article.status == "publishing"
    assert attempt.status == "publishing"
    assert invocation.status == "QUEUED"
    assert invocation.finished_at is None
    assert db.commit_count == 0
