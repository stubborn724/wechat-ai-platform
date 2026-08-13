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

    def __init__(self, article, attempt):
        super().__init__()
        self.article = article
        self.attempt = attempt

    def query(self, model_class):
        from app.models.mysql_models import Article, ContentVersion, PublishAttempt

        if model_class is Article:
            return _FakePollingQuery([self.article])
        if model_class is ContentVersion:
            return _FakePollingQuery([SimpleNamespace(job_id=self.attempt.job_id)])
        if model_class is PublishAttempt:
            return _FakePollingQuery([self.attempt])
        raise AssertionError(f"unexpected query model: {model_class}")

    def close(self):
        return None


def test_relay_submission_without_status_endpoint_becomes_observable_failure(monkeypatch):
    """relay 无状态查询能力时，超时发布必须退出 PUBLISHING 并保留诊断码。"""

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

    result = job_tasks.poll_publishing_articles.run()

    assert result.get("failed_unresolved") == 1
    assert article.status == "failed"
    assert article.phase == "PUBLISH_STATUS_UNAVAILABLE"
    assert attempt.status == "failed"
    assert attempt.error_code == "PUBLISH_STATUS_UNAVAILABLE"
