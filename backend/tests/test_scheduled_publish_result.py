"""定时任务微信公众号交付结果测试。"""

from types import SimpleNamespace

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
