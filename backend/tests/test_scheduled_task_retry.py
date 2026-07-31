"""定时任务失败恢复与有限重试的回归测试。

这些测试只验证状态机和 Celery 配置，不调用真实 ERP、模型或微信接口。
定时任务的关键可靠性边界必须在外部服务不可用时仍能独立验证，避免把网络
波动误判成业务逻辑正确。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只使用内存替身，不连接或清理业务数据库。"""

    yield


def test_scheduled_retry_uses_bounded_backoff_and_four_total_attempts():
    """初次执行失败后只允许三次重试，间隔必须固定为 2/5/15 分钟。"""

    from app.tasks import scheduled_task_executor as executor

    assert executor.SCHEDULED_TASK_RETRY_DELAYS == (120, 300, 900)
    assert executor.SCHEDULED_TASK_MAX_ATTEMPTS == 4
    assert executor.SCHEDULED_RUN_STALE_SECONDS == 30 * 60
    assert [
        executor.get_scheduled_retry_delay(attempt)
        for attempt in (1, 2, 3)
    ] == [120, 300, 900]


def test_only_transient_scheduled_errors_are_retryable():
    """认证、配置和参数错误不能反复重试，网络/超时错误可以重试。"""

    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageProviderError,
    )
    from app.services.erp_product_service import ErpProductApiError
    from app.tasks.scheduled_task_executor import is_retryable_scheduled_error

    assert is_retryable_scheduled_error(TimeoutError("上游超时")) is True
    assert is_retryable_scheduled_error(
        ImageProviderError(
            "上游暂时不可用",
            category=ImageErrorCategory.UPSTREAM,
            provider="test-provider",
        )
    ) is True
    assert is_retryable_scheduled_error(
        ImageProviderError(
            "密钥无效",
            category=ImageErrorCategory.AUTHENTICATION,
            provider="test-provider",
        )
    ) is False
    assert is_retryable_scheduled_error(ErpProductApiError("ERP Token 获取失败：凭证无效")) is False
    assert is_retryable_scheduled_error(ValueError("任务配置错误")) is False
    assert is_retryable_scheduled_error(OSError("本地磁盘写入失败")) is False


def test_ambiguous_wechat_publish_result_is_not_automatically_retried():
    """外部发布请求的响应不明确时必须停止自动重投，防止微信收到重复文章。"""
    from app.services.wechat_publisher import WechatPublishAmbiguousError
    from app.tasks.scheduled_task_executor import is_retryable_scheduled_error

    error = WechatPublishAmbiguousError("微信发布请求已发出，但响应连接中断")

    assert is_retryable_scheduled_error(error) is False


def test_retryable_run_is_scheduled_with_next_retry_time():
    """可恢复异常必须写入 retrying 和下一次重试时间，而不是直接失败。"""

    from app.tasks.scheduled_task_executor import mark_scheduled_run_retry

    class FakeDb:
        """只记录提交动作，验证状态变更确实落库。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    now = datetime(2026, 7, 31, 12, 0, 0)
    run = SimpleNamespace(
        status="running",
        attempt_count=1,
        next_retry_at=None,
        error_message=None,
        finished_at=None,
    )
    db = FakeDb()

    should_retry = mark_scheduled_run_retry(
        db,
        run,
        TimeoutError("图片中转站超时"),
        now=now,
    )

    assert should_retry is True
    assert run.status == "retrying"
    assert run.next_retry_at == now + timedelta(seconds=120)
    assert "图片中转站超时" in run.error_message
    assert run.finished_at is None
    assert db.commits == 1


def test_retry_limit_marks_run_failed_without_another_retry():
    """达到总尝试次数后必须停止重试并保留最终错误，避免无限循环。"""

    from app.tasks.scheduled_task_executor import mark_scheduled_run_retry

    class FakeDb:
        """记录状态落库次数。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    now = datetime(2026, 7, 31, 12, 20, 0)
    run = SimpleNamespace(
        status="running",
        attempt_count=4,
        next_retry_at=now,
        error_message=None,
        finished_at=None,
    )
    db = FakeDb()

    should_retry = mark_scheduled_run_retry(
        db,
        run,
        TimeoutError("连续超时"),
        now=now,
    )

    assert should_retry is False
    assert run.status == "failed"
    assert run.next_retry_at is None
    assert run.finished_at == now
    assert "连续超时" in run.error_message
    assert db.commits == 1


def test_non_retryable_run_fails_immediately():
    """不可恢复的配置错误必须立即失败，不应等待重试窗口。"""

    from app.tasks.scheduled_task_executor import mark_scheduled_run_retry

    class FakeDb:
        """记录状态落库次数。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    now = datetime(2026, 7, 31, 12, 20, 0)
    run = SimpleNamespace(
        status="running",
        attempt_count=1,
        next_retry_at=None,
        error_message=None,
        finished_at=None,
    )
    db = FakeDb()

    should_retry = mark_scheduled_run_retry(
        db,
        run,
        ValueError("公众号配置无效"),
        now=now,
    )

    assert should_retry is False
    assert run.status == "failed"
    assert run.finished_at == now
    assert db.commits == 1


def test_stale_scheduled_run_can_be_recovered_but_fresh_run_is_left_alone():
    """只有超过保护窗口的 queued/running 执行记录才允许补偿接管。"""

    from app.tasks.scheduled_task_executor import should_recover_scheduled_run

    now = datetime(2026, 7, 31, 12, 30, 0)
    fresh = SimpleNamespace(
        status="running",
        started_at=now - timedelta(minutes=5),
        created_at=now - timedelta(minutes=5),
        next_retry_at=None,
    )
    stale = SimpleNamespace(
        status="running",
        started_at=now - timedelta(minutes=40),
        created_at=now - timedelta(minutes=40),
        next_retry_at=None,
    )

    assert should_recover_scheduled_run(fresh, now=now) is False
    assert should_recover_scheduled_run(stale, now=now) is True


def test_queued_recovery_uses_latest_enqueue_time_not_original_creation_time():
    """重新排队后，Beat 应按本次派发时间计保护窗口，不能马上重复派发。"""

    from app.tasks.scheduled_task_executor import should_recover_scheduled_run

    now = datetime(2026, 7, 31, 12, 30, 0)
    fresh = SimpleNamespace(
        status="queued",
        created_at=now - timedelta(days=1),
        started_at=None,
        next_retry_at=now - timedelta(minutes=5),
    )
    stale = SimpleNamespace(
        status="queued",
        created_at=now - timedelta(days=1),
        started_at=None,
        next_retry_at=now - timedelta(minutes=40),
    )

    assert should_recover_scheduled_run(fresh, now=now) is False
    assert should_recover_scheduled_run(stale, now=now) is True


def test_article_delivery_completion_is_idempotent_for_retry():
    """重试前若文章已完成微信交付，不能再次调用发布接口。"""

    from app.tasks.scheduled_task_executor import is_article_delivery_complete

    assert is_article_delivery_complete(
        SimpleNamespace(status="draft_saved", publish_id=None, msg_data_id=None),
        "draft",
    ) is True
    assert is_article_delivery_complete(
        SimpleNamespace(status="published", publish_id=None, msg_data_id=None),
        "direct",
    ) is True
    assert is_article_delivery_complete(
        SimpleNamespace(status="generated", publish_id=None, msg_data_id=None),
        "direct",
    ) is False


def test_celery_requeues_scheduled_task_when_worker_is_lost():
    """Worker 崩溃时消息必须回到 Broker，避免只留下 running 数据。"""

    from app.celery_app import celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
