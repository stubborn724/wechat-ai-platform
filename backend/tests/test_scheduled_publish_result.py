"""定时任务微信公众号交付结果测试。"""

from types import SimpleNamespace
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """发布边界使用内存替身，不访问业务数据库。"""
    yield


def test_publish_to_wechat_raises_when_draft_save_fails(monkeypatch):
    """任一公众号保存失败都必须带账号 ID 向上抛出。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    def failing_publish(*args, **kwargs):
        """模拟微信中转站拒绝本地 HTTP 图片。"""
        raise RuntimeError("微信中转站无法访问图片")

    monkeypatch.setattr(wechat_publisher, "publish_article", failing_publish)
    task = SimpleNamespace(tenant_id=107, created_by=9)

    with pytest.raises(RuntimeError, match="公众号 #103"):
        _publish_to_wechat(
            db=SimpleNamespace(),
            article=SimpleNamespace(id=20),
            account_ids=[103],
            publish_mode="draft",
            task=task,
        )


def test_publish_to_wechat_rejects_partial_direct_publish(monkeypatch):
    """直接发布只保存草稿但提交失败时，不能把文章误标为已发布。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    monkeypatch.setattr(
        wechat_publisher,
        "publish_article",
        lambda *args, **kwargs: {
            "media_id": "draft-1",
            "publish_id": None,
            "draft_saved": True,
            "publish_error": "微信接口暂时不可用",
        },
    )

    with pytest.raises(RuntimeError, match="正式发布失败"):
        _publish_to_wechat(
            db=SimpleNamespace(),
            article=SimpleNamespace(id=21),
            account_ids=[103],
            publish_mode="direct",
            task=SimpleNamespace(tenant_id=107, created_by=9),
        )


def test_publish_to_wechat_persists_delivery_identifiers(monkeypatch):
    """成功交付的微信标识必须回写文章，供重复消息做幂等判断。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    monkeypatch.setattr(
        wechat_publisher,
        "publish_article",
        lambda *args, **kwargs: {
            "media_id": "draft-2",
            "publish_id": "publish-2",
            "draft_saved": True,
        },
    )
    article = SimpleNamespace(id=23, publish_id=None, msg_data_id=None, wechat_account_id=None)

    _publish_to_wechat(
        db=SimpleNamespace(),
        article=article,
        account_ids=[103],
        publish_mode="direct",
        task=SimpleNamespace(tenant_id=107, created_by=9),
    )

    assert article.publish_id == "publish-2"
    assert article.wechat_account_id == 103


def test_publish_to_wechat_propagates_private_domain_and_persists_msg_id(monkeypatch):
    """定时任务私域交付必须传递发布域，并以 msg_id 作为成功凭证。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    calls = []

    def publish_private(*args, **kwargs):
        calls.append(kwargs)
        return {"msg_id": "msg-private-1", "relay_status": "FOLLOWER_PUSH_SENT"}

    monkeypatch.setattr(wechat_publisher, "publish_article", publish_private)
    article = SimpleNamespace(
        id=26,
        publish_id=None,
        msg_data_id=None,
        wechat_account_id=None,
        publish_domain=None,
    )
    run = SimpleNamespace(id=46, delivery_results={}, publish_domain="private")

    _publish_to_wechat(
        db=SimpleNamespace(commit=lambda: None),
        article=article,
        account_ids=[103],
        publish_mode="direct",
        task=SimpleNamespace(tenant_id=107, created_by=9, publish_domain="public"),
        run=run,
    )

    assert calls[0]["publish_domain"] == "private"
    assert article.msg_data_id == "msg-private-1"
    assert article.publish_id is None
    assert article.publish_domain == "private"
    assert run.delivery_results["26:103"] == {
        "status": "success",
        "mode": "direct",
        "publish_domain": "private",
        "msg_data_id": "msg-private-1",
    }


def test_scheduled_delivery_does_not_reuse_result_from_another_publish_domain():
    """公域和私域结果不能共用同一条重试幂等记录。"""
    from app.tasks.scheduled_task_executor import _is_successful_scheduled_delivery

    result = {
        "26:103": {
            "status": "success",
            "mode": "direct",
            "publish_domain": "public",
        }
    }

    assert _is_successful_scheduled_delivery(
        result,
        article_id=26,
        account_id=103,
        publish_mode="direct",
        publish_domain="public",
    ) is True
    assert _is_successful_scheduled_delivery(
        result,
        article_id=26,
        account_id=103,
        publish_mode="direct",
        publish_domain="private",
    ) is False

    legacy_result = {
        "26:103": {
            "status": "success",
            "mode": "direct",
        }
    }
    assert _is_successful_scheduled_delivery(
        legacy_result,
        article_id=26,
        account_id=103,
        publish_mode="direct",
        publish_domain="private",
    ) is False


def test_historical_public_article_cannot_short_circuit_private_delivery():
    """没有发布域快照的历史公域文章不能阻止新的私域交付。"""
    from app.tasks.scheduled_task_executor import is_article_delivery_complete

    article = SimpleNamespace(
        status="published",
        publish_id="old-public-publish-id",
        msg_data_id=None,
        publish_domain=None,
    )

    assert is_article_delivery_complete(article, "direct", "public") is True
    assert is_article_delivery_complete(article, "direct", "private") is False


def test_publish_to_wechat_rejects_unknown_mode_before_external_call(monkeypatch):
    """非法发布模式必须在调用微信前失败，避免错误配置产生外部副作用。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    calls = []

    def unexpected_publish(*args, **kwargs):
        """记录不应发生的外部调用。"""
        calls.append((args, kwargs))
        return {"media_id": "unexpected"}

    monkeypatch.setattr(wechat_publisher, "publish_article", unexpected_publish)

    with pytest.raises(ValueError, match="不支持的定时任务发布模式"):
        _publish_to_wechat(
            db=SimpleNamespace(),
            article=SimpleNamespace(id=24),
            account_ids=[103],
            publish_mode="invalid",
            task=SimpleNamespace(tenant_id=107, created_by=9),
        )

    assert calls == []


def test_publish_to_wechat_records_each_account_and_skips_completed_delivery(monkeypatch):
    """重试同一篇文章时只应发布尚未成功的公众号，避免多账号重复交付。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    calls = []

    def publish_for_account(*args, **kwargs):
        """为每个账号返回独立草稿标识，模拟多账号发布。"""
        account_id = args[2]
        calls.append(account_id)
        return {"media_id": f"draft-{account_id}"}

    monkeypatch.setattr(wechat_publisher, "publish_article", publish_for_account)

    class FakeDb:
        """只记录账号级结果写入所需的提交动作。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    db = FakeDb()
    run = SimpleNamespace(
        id=45,
        delivery_results={
            "24:103": {
                "status": "success",
                "mode": "draft",
                "media_id": "draft-existing",
            }
        },
    )
    article = SimpleNamespace(
        id=24,
        publish_id=None,
        msg_data_id=None,
        wechat_account_id=None,
    )

    _publish_to_wechat(
        db=db,
        article=article,
        account_ids=[103, 104],
        publish_mode="draft",
        task=SimpleNamespace(tenant_id=107, created_by=9),
        run=run,
    )

    assert calls == [104]
    assert run.delivery_results["24:104"] == {
        "status": "success",
        "mode": "draft",
        "publish_domain": "public",
        "media_id": "draft-104",
    }
    assert article.wechat_account_id == 104
    assert db.commits == 1


def test_pending_draft_delivery_accounts_skip_already_successful_accounts():
    """草稿重试只提交没有成功记录的账号，避免并发优化破坏投递幂等。"""
    from app.services.scheduled_delivery_service import pending_draft_delivery_account_ids

    delivery_results = {
        "24:103": {
            "status": "success",
            "mode": "draft",
            "publish_domain": "public",
            "media_id": "draft-existing",
        }
    }

    assert pending_draft_delivery_account_ids(
        article_id=24,
        account_ids=[103, 104, 107],
        delivery_results=delivery_results,
        publish_domain="public",
    ) == [104, 107]


def test_bounded_draft_delivery_never_exceeds_configured_parallelism():
    """五账号草稿保存最多双并发，避免并发过高反而触发微信或中转站限流。"""
    from app.services.scheduled_delivery_service import execute_bounded_draft_deliveries

    active_calls = 0
    peak_calls = 0
    lock = threading.Lock()

    def deliver(account_id: int) -> str:
        """用短暂阻塞模拟微信请求并记录实际并行度。"""
        nonlocal active_calls, peak_calls
        with lock:
            active_calls += 1
            peak_calls = max(peak_calls, active_calls)
        time.sleep(0.03)
        with lock:
            active_calls -= 1
        return f"draft-{account_id}"

    results = execute_bounded_draft_deliveries(
        [103, 104, 107, 108, 109],
        max_workers=2,
        deliver=deliver,
    )

    assert peak_calls == 2
    assert [result.account_id for result in results] == [103, 104, 107, 108, 109]
    assert [result.value for result in results] == [
        "draft-103", "draft-104", "draft-107", "draft-108", "draft-109"
    ]
    assert all(result.error is None for result in results)


def test_publish_to_wechat_uses_account_isolated_workers_for_multi_account_drafts(monkeypatch):
    """多账号草稿必须委托独立工作单元，主会话不得跨线程复用。"""
    from app.tasks import scheduled_task_executor as executor

    calls = []

    def deliver_one(*, article_id, task_id, run_id, account_id, publish_domain):
        """模拟子线程独立完成账号级保存。"""
        calls.append((article_id, task_id, run_id, account_id, publish_domain))
        return {"media_id": f"draft-{account_id}"}

    monkeypatch.setattr(executor, "_publish_scheduled_draft_for_account", deliver_one)
    monkeypatch.setattr(
        executor.settings,
        "scheduled_draft_delivery_max_workers",
        2,
        raising=False,
    )
    run = SimpleNamespace(id=45, delivery_results={}, publish_domain="public")

    executor._publish_to_wechat(
        db=SimpleNamespace(),
        article=SimpleNamespace(id=24),
        account_ids=[103, 104, 107],
        publish_mode="draft",
        task=SimpleNamespace(id=8, tenant_id=107, created_by=9, publish_domain="public"),
        run=run,
    )

    assert calls == [
        (24, 8, 45, 103, "public"),
        (24, 8, 45, 104, "public"),
        (24, 8, 45, 107, "public"),
    ]


def test_partial_direct_publish_is_recorded_before_error(monkeypatch):
    """直接发布只拿到草稿 ID 时要保存部分结果，方便人工处理而非盲目重投。"""
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    monkeypatch.setattr(
        wechat_publisher,
        "publish_article",
        lambda *args, **kwargs: {
            "media_id": "draft-partial",
            "publish_id": None,
            "draft_saved": True,
            "publish_error": "正式发布接口失败",
        },
    )

    class FakeDb:
        """记录部分成功状态是否先于异常落库。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    run = SimpleNamespace(id=45, delivery_results={})
    with pytest.raises(RuntimeError, match="正式发布失败"):
        _publish_to_wechat(
            db=FakeDb(),
            article=SimpleNamespace(id=25, publish_id=None, msg_data_id=None),
            account_ids=[103],
            publish_mode="direct",
            task=SimpleNamespace(tenant_id=107, created_by=9),
            run=run,
        )

    assert run.delivery_results["25:103"] == {
        "status": "partial",
        "mode": "direct",
        "publish_domain": "public",
        "media_id": "draft-partial",
        "error": "正式发布接口失败",
    }


def test_scheduled_media_article_is_bound_before_publish(monkeypatch):
    """纯图片和视频共用的文章落库辅助方法必须先绑定运行记录。"""
    from app.tasks import scheduled_task_executor as executor

    events = []

    class FakeDb:
        """模拟文章创建流程的最小数据库接口。"""

        def add(self, article):
            events.append("add")

        def flush(self):
            events.append("flush")

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(
        executor,
        "_bind_scheduled_run_article",
        lambda db, run_id, article_id: events.append(("bind", run_id, article_id)),
    )
    article = SimpleNamespace(id=26)

    executor._persist_scheduled_article(FakeDb(), article, run_id=45)

    assert events == ["add", "flush", ("bind", 45, 26), "commit"]


def test_publish_success_is_not_overridden_by_console_encoding(monkeypatch):
    """外部交付成功后，控制台编码问题不能反向把任务标记为失败。"""
    import builtins
    from app.services import wechat_publisher
    from app.tasks.scheduled_task_executor import _publish_to_wechat

    monkeypatch.setattr(wechat_publisher, "publish_article", lambda *args, **kwargs: {"media_id": "draft-1"})
    monkeypatch.setattr(
        builtins,
        "print",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            UnicodeEncodeError("gbk", "✅", 0, 1, "unsupported")
        ),
    )

    _publish_to_wechat(
        db=SimpleNamespace(),
        article=SimpleNamespace(id=22),
        account_ids=[103],
        publish_mode="draft",
        task=SimpleNamespace(tenant_id=107, created_by=9),
    )


def test_scheduled_executor_configures_console_output_for_windows_gbk(monkeypatch):
    """定时任务模块本身也必须修正输出编码，直接执行 Worker 入口时不能依赖 FastAPI。"""
    from app.tasks import scheduled_task_executor

    class FakeStream:
        """模拟 Windows GBK stdout/stderr，并记录 reconfigure 调用参数。"""

        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr(scheduled_task_executor.sys, "stdout", stdout)
    monkeypatch.setattr(scheduled_task_executor.sys, "stderr", stderr)

    scheduled_task_executor.configure_safe_console_output()

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_generated_image_is_preferred_over_footer_as_cover():
    """文章封面应优先使用本次生成图，不能误选 HTML 末尾的页脚图片。"""
    from app.tasks.scheduled_task_executor import _select_article_cover

    state = SimpleNamespace(images=[SimpleNamespace(url="https://dashscope.example.com/generated.png")])
    html = '<img src="http://localhost:9002/wechat-assets/footer.png">'

    assert _select_article_cover(state, html) == "https://dashscope.example.com/generated.png"


def test_completed_scheduled_run_is_not_reexecuted_when_redis_redelivers_message():
    """已完成的运行记录收到重复消息时必须幂等跳过，不能重复保存草稿。"""
    from app.tasks.scheduled_task_executor import is_completed_scheduled_run

    completed_run = SimpleNamespace(status="completed", article_id=50)
    incomplete_run = SimpleNamespace(status="completed", article_id=None)

    assert is_completed_scheduled_run(completed_run) is True
    assert is_completed_scheduled_run(incomplete_run) is False


def test_scheduled_article_module_exposes_image_result_for_poster_pipeline():
    """纯海报分支在模块级流水线中构造图片结果，不能依赖 Celery 入口的局部导入。"""
    from app.schemas.article import ImageResult as SchemaImageResult
    from app.tasks import scheduled_task_executor

    assert scheduled_task_executor.ImageResult is SchemaImageResult


def test_cover_fallback_supports_single_quoted_html_source():
    """没有生成结果元数据时，HTML 单引号图片仍可作为封面兜底。"""
    from app.tasks.scheduled_task_executor import _select_article_cover

    state = SimpleNamespace(images=[])
    html = "<img src='https://example.com/first.png'>"

    assert _select_article_cover(state, html) == "https://example.com/first.png"


def test_cover_fallback_prefers_html_body_image_over_markdown_footer():
    """恢复任务缺少图片元数据时，也不能让 Markdown 本地页脚抢占正文封面。"""
    from app.tasks.scheduled_task_executor import _select_article_cover

    state = SimpleNamespace(images=[])
    content = (
        "![页脚](http://localhost:9002/wechat-assets/footer.png)\n"
        '<section><img src="https://dashscope.example.com/generated.png?a=1&amp;b=2"></section>'
    )

    assert _select_article_cover(state, content) == "https://dashscope.example.com/generated.png?a=1&b=2"


@pytest.mark.parametrize(
    ("publish_mode", "expected_status", "expected_phase"),
    [
        ("draft", "draft_saved", "DRAFT_SAVED"),
        ("direct", "published", "PUBLISHED"),
    ],
)
def test_finalize_article_status_after_wechat_success(
    publish_mode,
    expected_status,
    expected_phase,
):
    """只有发布调用返回成功后，公共收口方法才能写最终交付状态。"""
    from app.tasks.scheduled_task_executor import _finalize_article_delivery

    class FakeDb:
        """记录事务提交次数，确保最终状态落库。"""

        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    db = FakeDb()
    article = SimpleNamespace(status="generated", phase="CONTENT_GENERATED")

    _finalize_article_delivery(db, article, publish_mode)

    assert article.status == expected_status
    assert article.phase == expected_phase
    assert db.commits == 1
