"""TaGeAI 投递状态收敛回归测试。

测试通过发布器和数据库替身覆盖失败分支，确保测试期间不调用真实微信 API、
中转站或数据库。关注点是平台对外状态必须忠实反映投递事实。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件使用对象替身，不依赖全局 MySQL 清理夹具。"""

    yield


class _FakeDeliveryDb:
    """记录交付对象写入和提交次数的最小数据库替身。"""

    def __init__(self):
        self.added = []
        self.commit_count = 0

    def add(self, value):
        self.added.append(value)

    def flush(self):
        # 生产 ORM 会在 flush 后回填主键；替身也要满足日志和关联字段的这一前提。
        for index, value in enumerate(self.added, start=1):
            if getattr(value, "id", None) is None:
                value.id = index
        return None

    def commit(self):
        self.commit_count += 1

    def query(self, _model_class):
        """提供取消探针所需的最新任务状态查询。"""

        return self

    def filter(self, *args, **kwargs):
        """保持 SQLAlchemy 链式调用，当前夹具固定模拟未取消任务。"""

        return self

    def scalar(self):
        """投递生命周期测试默认运行中，取消分支由专门用例覆盖。"""

        return "generating"


def _job_with_delivery_mode(delivery_mode):
    """构造只包含文章投递所需字段的内容任务。"""

    return SimpleNamespace(
        id=901,
        tenant_id=7,
        created_by=11,
        account_id=103,
        topic="人体工学椅",
        generation_config={
            "publish_mode": delivery_mode,
            "account_ids": [103],
            "style": "default",
        },
    )


def _generated_version():
    """构造一条已经生成出正文的内容版本。"""

    return SimpleNamespace(
        id=1001,
        title="测试文章",
        summary="测试摘要",
        body_markdown="测试正文",
        model_metadata=None,
        article_content_type="image_text",
    )


def _objects_by_type(db, model_class):
    """从替身数据库中筛选指定 ORM 类型对象。"""

    return [value for value in db.added if isinstance(value, model_class)]


def test_draft_save_failure_marks_article_and_attempt_as_failed(monkeypatch):
    """草稿保存异常必须是 FAILED，而不是预先写入的 DRAFT_SAVED。"""

    from app.models.mysql_models import Article, PublishAttempt
    from app.services import wechat_publisher
    from app.tasks.job_tasks import _save_versions_as_articles_and_drafts

    def raise_draft_error(*args, **kwargs):
        raise RuntimeError("草稿箱不可用")

    monkeypatch.setattr(wechat_publisher, "save_article_as_draft", raise_draft_error)
    db = _FakeDeliveryDb()

    _save_versions_as_articles_and_drafts(db, _job_with_delivery_mode("draft"), [_generated_version()])

    article = _objects_by_type(db, Article)[0]
    attempt = _objects_by_type(db, PublishAttempt)[0]
    assert article.status == "failed"
    assert article.phase == "DRAFT_DELIVERY_FAILED"
    assert attempt.status == "failed"
    assert attempt.error_code == "DRAFT_DELIVERY_FAILED"


def test_direct_publish_partial_response_is_terminal_failure_not_publishing(monkeypatch):
    """已保存草稿但未获得 publishId 时必须明确失败，不能无限发布中。"""

    from app.models.mysql_models import Article, PublishAttempt
    from app.services import wechat_publisher
    from app.tasks.job_tasks import _save_versions_as_articles_and_drafts

    monkeypatch.setattr(
        wechat_publisher,
        "publish_article",
        lambda *args, **kwargs: {
            "media_id": "draft-partial",
            "publish_id": None,
            "draft_saved": True,
            "publish_error": "微信正式发布失败",
        },
    )
    db = _FakeDeliveryDb()

    _save_versions_as_articles_and_drafts(db, _job_with_delivery_mode("direct"), [_generated_version()])

    article = _objects_by_type(db, Article)[0]
    attempt = _objects_by_type(db, PublishAttempt)[0]
    assert article.publish_id == "draft-partial"
    assert article.status == "failed"
    assert article.phase == "PUBLISH_SUBMISSION_FAILED"
    assert attempt.status == "failed"
    assert attempt.error_code == "PUBLISH_SUBMISSION_FAILED"


def test_relay_publish_submission_is_marked_as_relay_publishing(monkeypatch):
    """中转站只确认受理时必须保留可识别的待终态阶段。"""

    from app.models.mysql_models import Article, PublishAttempt
    from app.services import wechat_publisher
    from app.tasks.job_tasks import _save_versions_as_articles_and_drafts

    monkeypatch.setattr(
        wechat_publisher,
        "publish_article",
        lambda *args, **kwargs: {
            "publish_id": "relay-publish-001",
            "relay_status": "PUBLIC_PUBLISH_SUBMITTED",
        },
    )
    db = _FakeDeliveryDb()

    _save_versions_as_articles_and_drafts(db, _job_with_delivery_mode("direct"), [_generated_version()])

    article = _objects_by_type(db, Article)[0]
    attempt = _objects_by_type(db, PublishAttempt)[0]
    assert article.status == "publishing"
    assert article.phase == "RELAY_PUBLISHING"
    assert attempt.status == "publishing"


class _FakePollingQuery:
    """支持 relay 发布轮询分支所需的链式查询。"""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _FakePollingDb(_FakeDeliveryDb):
    """为 relay 状态收敛提供文章和发布尝试记录。"""

    def __init__(self, article, attempt, candidate=None):
        super().__init__()
        self.article = article
        self.attempt = attempt
        self.candidate = candidate

    def query(self, model_class):
        from app.models.mysql_models import Article, ContentVersion, PublishAttempt, TageAiPublishCandidate

        if model_class is Article:
            return _FakePollingQuery([self.article])
        if model_class is ContentVersion:
            return _FakePollingQuery([SimpleNamespace(job_id=self.attempt.job_id)])
        if model_class is PublishAttempt:
            return _FakePollingQuery([self.attempt])
        if model_class is TageAiPublishCandidate:
            return _FakePollingQuery([self.candidate] if self.candidate else [])
        raise AssertionError(f"unexpected query model: {model_class}")

    def close(self):
        return None


class _CandidateBoundPollingQuery(_FakePollingQuery):
    """模拟正式发布任务与预览版本使用不同 Job 时的投递记录过滤。"""

    def __init__(self, attempt):
        super().__init__([])
        self.attempt = attempt

    def filter(self, *args, **kwargs):
        """只有查询显式按发布候选幂等键关联时才返回正式发布尝试。"""

        # 正式发布的 PublishAttempt 归属新建的 ``article_publish_existing`` Job，不能
        # 仅依赖预览 ContentVersion 的旧 job_id。该替身使测试在旧实现下无法取到尝试。
        if any("idempotency_key" in str(condition) for condition in args):
            self.rows = [self.attempt]
        else:
            self.rows = []
        return self


class _CandidateBoundPollingDb(_FakePollingDb):
    """验证轮询通过候选幂等键找到正式发布 Job 的投递记录。"""

    def __init__(self, article, attempt, candidate, preview_job_id):
        super().__init__(article, attempt, candidate)
        self.preview_job_id = preview_job_id

    def query(self, model_class):
        from app.models.mysql_models import Article, ContentVersion, PublishAttempt, TageAiPublishCandidate

        if model_class is Article:
            return _FakePollingQuery([self.article])
        if model_class is ContentVersion:
            return _FakePollingQuery([SimpleNamespace(job_id=self.preview_job_id)])
        if model_class is PublishAttempt:
            return _CandidateBoundPollingQuery(self.attempt)
        if model_class is TageAiPublishCandidate:
            return _FakePollingQuery([self.candidate])
        raise AssertionError(f"unexpected query model: {model_class}")


def test_relay_submission_without_status_endpoint_becomes_observable_unknown(monkeypatch):
    """relay 无状态查询能力时，超时发布必须进入可恢复的未知状态。"""

    from app.services import wechat_gateway_policy
    from app.tasks import job_tasks

    stale_time = datetime.now(timezone.utc) - timedelta(hours=1)
    article = SimpleNamespace(
        id=1002,
        tenant_id=7,
        status="publishing",
        phase="RELAY_PUBLISHING",
        publish_id="relay-publish-001",
        error_message=None,
        updated_at=stale_time,
        wechat_publish_time=stale_time,
    )
    attempt = SimpleNamespace(
        id=2002,
        tenant_id=7,
        job_id=901,
        account_id=103,
        status="publishing",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    db = _FakePollingDb(article, attempt)
    monkeypatch.setattr(job_tasks, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(wechat_gateway_policy, "is_wechat_relay_enabled", lambda: True)
    monkeypatch.setattr(
        job_tasks,
        "_create_wechat_relay_client",
        lambda: SimpleNamespace(query_publish_status=lambda _publish_id: (_ for _ in ()).throw(RuntimeError("404"))),
        raising=False,
    )

    result = job_tasks.poll_publishing_articles.run()

    assert result.get("unknown_unresolved") == 1
    assert article.status == "unknown"
    assert article.phase == "PUBLISH_STATUS_UNKNOWN"
    assert attempt.status == "unknown"
    assert attempt.error_code == "PUBLISH_STATUS_UNKNOWN"


def test_relay_publish_status_query_marks_article_attempt_and_candidate_published(monkeypatch):
    """中转站确认已发布后，轮询必须收敛全部本地事实且不重新提交文章。"""

    from app.services import wechat_gateway_policy
    from app.tasks import job_tasks

    submitted_at = datetime.now(timezone.utc)
    article = SimpleNamespace(
        id=1003,
        tenant_id=7,
        status="publishing",
        phase="RELAY_PUBLISHING",
        publish_id="relay-publish-002",
        msg_data_id=None,
        error_message=None,
        updated_at=submitted_at,
        wechat_publish_time=submitted_at,
    )
    attempt = SimpleNamespace(
        id=2003,
        tenant_id=7,
        job_id=901,
        account_id=103,
        status="publishing",
        error_code=None,
        error_message=None,
        platform_message_id=None,
        finished_at=None,
    )
    candidate = SimpleNamespace(id=3003, article_id=1003, status="PUBLISHING")
    db = _FakePollingDb(article, attempt, candidate)
    queried_publish_ids = []
    relay_client = SimpleNamespace(
        query_publish_status=lambda publish_id: (
            queried_publish_ids.append(publish_id)
            or {
                "relay_status": "PUBLISHED",
                "wechat_article_id": "wechat-article-002",
                "wechat_url": "https://mp.weixin.qq.com/s/published",
                "message": "published",
                "error_code": None,
            }
        ),
    )
    monkeypatch.setattr(job_tasks, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(wechat_gateway_policy, "is_wechat_relay_enabled", lambda: True)
    monkeypatch.setattr(job_tasks, "_create_wechat_relay_client", lambda: relay_client, raising=False)

    result = job_tasks.poll_publishing_articles.run()

    assert queried_publish_ids == ["relay-publish-002"]
    assert result["published"] == 1
    assert article.status == "published"
    assert article.phase == "PUBLISHED"
    assert article.msg_data_id == "wechat-article-002"
    assert attempt.status == "success"
    assert attempt.platform_message_id == "wechat-article-002"
    assert attempt.finished_at is not None
    assert candidate.status == "PUBLISHED"


def test_relay_publish_status_updates_attempt_from_formal_publish_job(monkeypatch):
    """预览版本与正式发布 Job 不同，仍须同步正式发布尝试的终态。"""

    from app.services import wechat_gateway_policy
    from app.tasks import job_tasks

    submitted_at = datetime.now(timezone.utc)
    article = SimpleNamespace(
        id=1004,
        tenant_id=7,
        status="publishing",
        phase="RELAY_PUBLISHING",
        publish_id="relay-publish-003",
        msg_data_id=None,
        error_message=None,
        updated_at=submitted_at,
        wechat_publish_time=submitted_at,
    )
    # 预览 Job 为 901，正式发布任务单独创建了 902；这是实际 Phase B 流程的正常形态。
    attempt = SimpleNamespace(
        id=2004,
        tenant_id=7,
        job_id=902,
        account_id=103,
        status="publishing",
        error_code=None,
        error_message=None,
        platform_message_id=None,
        finished_at=None,
    )
    candidate = SimpleNamespace(id=3004, article_id=1004, status="PUBLISHING")
    db = _CandidateBoundPollingDb(article, attempt, candidate, preview_job_id=901)
    relay_client = SimpleNamespace(
        query_publish_status=lambda _publish_id: {
            "relay_status": "PUBLISHED",
            "wechat_article_id": "wechat-article-003",
            "message": "published",
            "error_code": None,
        },
    )
    monkeypatch.setattr(job_tasks, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(wechat_gateway_policy, "is_wechat_relay_enabled", lambda: True)
    monkeypatch.setattr(job_tasks, "_create_wechat_relay_client", lambda: relay_client, raising=False)

    result = job_tasks.poll_publishing_articles.run()

    assert result["published"] == 1
    assert article.status == "published"
    assert attempt.status == "success"
    assert attempt.platform_message_id == "wechat-article-003"
    assert attempt.finished_at is not None
