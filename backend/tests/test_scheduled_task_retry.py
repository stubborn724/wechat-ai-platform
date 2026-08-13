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
    assert executor.SCHEDULED_QUEUED_STALE_SECONDS == 5 * 60
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


def test_waiting_queued_run_is_not_recovered_when_it_has_not_been_dispatched():
    """真正等待队列的记录可以排很久，不能被五分钟窗口误判为丢消息。"""

    from app.tasks.scheduled_task_executor import should_recover_scheduled_run

    now = datetime(2026, 7, 31, 12, 30, 0)
    waiting = SimpleNamespace(
        status="queued",
        attempt_count=0,
        celery_task_id=None,
        next_retry_at=None,
        created_at=now - timedelta(hours=2),
        started_at=None,
    )

    assert should_recover_scheduled_run(waiting, now=now) is False


def test_dispatched_run_blocks_following_scheduled_runs():
    """已派发、执行中或等待重试的队头记录必须阻塞后续记录派发。"""

    from app.tasks.scheduled_task_executor import is_scheduled_run_in_flight

    dispatched = SimpleNamespace(
        status="queued",
        attempt_count=1,
        celery_task_id="celery-1",
        next_retry_at=datetime(2026, 7, 31, 12, 0, 0),
    )
    waiting = SimpleNamespace(
        status="queued",
        attempt_count=0,
        celery_task_id=None,
        next_retry_at=None,
    )
    retrying = SimpleNamespace(status="retrying")
    running = SimpleNamespace(status="running")
    completed = SimpleNamespace(status="completed")

    assert is_scheduled_run_in_flight(dispatched) is True
    assert is_scheduled_run_in_flight(retrying) is True
    assert is_scheduled_run_in_flight(running) is True
    assert is_scheduled_run_in_flight(waiting) is False
    assert is_scheduled_run_in_flight(completed) is False


def test_queue_selector_only_returns_the_oldest_waiting_run():
    """有队头在途时不派发任何后续记录，否则只取最早等待记录。"""

    from app.tasks.scheduled_task_executor import select_next_waiting_scheduled_run

    waiting_first = SimpleNamespace(
        id=1,
        scheduled_date="2026-07-31",
        scheduled_time="08:00",
        status="queued",
        attempt_count=0,
        celery_task_id=None,
        next_retry_at=None,
    )
    waiting_second = SimpleNamespace(
        id=2,
        scheduled_date="2026-07-31",
        scheduled_time="09:00",
        status="queued",
        attempt_count=0,
        celery_task_id=None,
        next_retry_at=None,
    )
    in_flight = SimpleNamespace(
        id=3,
        scheduled_date="2026-07-31",
        scheduled_time="07:00",
        status="running",
    )

    assert select_next_waiting_scheduled_run([waiting_second, waiting_first]) is waiting_first
    assert select_next_waiting_scheduled_run([waiting_first, in_flight]) is None


def test_queued_message_has_shorter_recovery_window_than_running_message():
    """消息未被 Worker 认领时应快速补投，已运行任务仍须保留长保护窗口。"""

    from app.tasks.scheduled_task_executor import should_recover_scheduled_run

    now = datetime(2026, 7, 31, 12, 30, 0)
    queued = SimpleNamespace(
        status="queued",
        created_at=now - timedelta(minutes=6),
        started_at=None,
        next_retry_at=now - timedelta(minutes=6),
    )
    running = SimpleNamespace(
        status="running",
        created_at=now - timedelta(minutes=6),
        started_at=now - timedelta(minutes=6),
        next_retry_at=None,
    )

    assert should_recover_scheduled_run(queued, now=now) is True
    assert should_recover_scheduled_run(running, now=now) is False


def test_scheduled_execution_is_routed_to_dedicated_queue_with_publish_retry():
    """定时任务应进入独立队列，并在 Redis 短暂不可用时自动重试投递。"""

    from app.celery_app import celery_app

    route = celery_app.conf.task_routes[
        "app.tasks.scheduled_task_executor.execute_scheduled_article"
    ]

    assert route["queue"] == "scheduled"
    assert celery_app.conf.task_publish_retry is True
    assert celery_app.conf.broker_transport_options["visibility_timeout"] >= 3600


def test_scheduled_run_dispatch_explicitly_targets_dedicated_queue(monkeypatch):
    """恢复中的运行记录必须显式投递到专用队列，不能依赖默认路由。

    定时检查任务运行在普通 Worker。若只依赖 Celery 的全局路由，在 Worker
    重启、积压恢复或配置热更新时，数据库可能已经记录“已投递”，但专用
    Worker 实际没有收到消息。派发入口因此必须把队列和路由键作为调用参数
    固定下来，保证任何恢复投递都进入 scheduled 队列。
    """

    from app.tasks import scheduled_task_executor as executor

    run = SimpleNamespace(
        id=125,
        task_id=20,
        status="queued",
        attempt_count=0,
        celery_task_id=None,
        next_retry_at=None,
        started_at=None,
        finished_at=None,
        error_message=None,
    )

    class FakeQuery:
        """模拟本用例需要的行锁查询链，避免连接真实业务数据库。"""

        def filter(self, *_args):
            return self

        def populate_existing(self):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return run

    class FakeDb:
        """仅记录提交次数；派发正确性不依赖真实事务实现。"""

        def __init__(self):
            self.commit_count = 0

        def query(self, *_args):
            return FakeQuery()

        def commit(self):
            self.commit_count += 1

    dispatched = {}

    def fake_apply_async(*, args, queue, routing_key, retry):
        dispatched.update(
            args=args,
            queue=queue,
            routing_key=routing_key,
            retry=retry,
        )
        return SimpleNamespace(id="scheduled-message-125")

    monkeypatch.setattr(executor.execute_scheduled_article, "apply_async", fake_apply_async)
    monkeypatch.setattr(
        executor.execute_scheduled_article,
        "delay",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不得回退到默认 delay 路由")),
    )

    assert executor._enqueue_scheduled_run(
        FakeDb(),
        task_id=20,
        run=run,
        reason="测试可靠投递",
        allow_fresh=True,
    ) is True
    assert dispatched == {
        "args": (20, 125),
        "queue": "scheduled",
        "routing_key": "scheduled",
        "retry": True,
    }
    assert run.celery_task_id == "scheduled-message-125"


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
