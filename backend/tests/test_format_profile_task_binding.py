"""格式模板与测试定时任务的隔离契约。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证纯策略，不连接业务数据库。"""

    yield


def test_unbound_task_keeps_legacy_pipeline_for_erp_production_task() -> None:
    """绣蔓等未绑定模板的正式任务必须继续走原有生成管线。"""

    from app.services.format_profile_task_policy import should_use_format_profile

    task = SimpleNamespace(name="绣蔓仿写", format_profile_id=None)

    assert should_use_format_profile(task) is False


def test_bound_testing_task_uses_explicit_profile_only() -> None:
    """测试任务只有显式绑定模板才启用新管线，不能按名称或知识库自动切换。"""

    from app.services.format_profile_task_policy import should_use_format_profile

    task = SimpleNamespace(name="HTML 19 图测试", format_profile_id=7)

    assert should_use_format_profile(task) is True


def test_xiuman_binding_only_updates_tasks_using_the_fixed_feed_article() -> None:
    """绣蔓模板绑定必须限定固定投喂源，不能误改其他 ERP 任务。"""

    from app.services.xiuman_format_profile_binding_service import (
        build_xiuman_format_profile_binding_updates,
    )

    matching_task = SimpleNamespace(
        id=11,
        name="绣蔓仿写-私域",
        writing_mode="feed",
        feed_article_ids=[1],
        format_profile_id=None,
    )
    unrelated_task = SimpleNamespace(
        id=13,
        name="绣蔓仿写-公域",
        writing_mode="feed",
        feed_article_ids=[2],
        format_profile_id=None,
    )

    updates = build_xiuman_format_profile_binding_updates(
        [matching_task, unrelated_task],
        source_article_id=1,
        format_profile_id=6,
    )

    assert updates == [(matching_task, 6)]


def test_format_profile_state_uses_saved_blueprint_without_reparsing_source_html(monkeypatch) -> None:
    """模板任务应从持久化蓝图恢复，不能再次解析原文章 HTML。"""

    import app.services.article_agent_service as article_agent_service
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.format_profile_service import analyze_feed_article_format

    profile = analyze_feed_article_format(
        article_id=44,
        article_title="模板文章",
        body_html='<section><p>原始正文</p><img src="https://cdn.example.com/a.jpg" /></section>',
    )

    def fail_if_reparsed(_html: str):
        raise AssertionError("格式模板任务不应重新解析原 HTML")

    async def fake_call_llm(*_args, **_kwargs):
        return (
            '{"wechat_title":"新的公众号标题","visual_title":"新的视觉标题",'
            '"visual_subtitle":"","text_slots":[{"id":"text-1","content":"新的正文"}],'
            '"image_slots":[]}'
        )

    monkeypatch.setattr(article_agent_service, "analyze_html_for_imitation", fail_if_reparsed, raising=False)
    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)

    state = ArticleState(
        task_id="profile-html-task",
        topic="测试主题",
        title=SelectedTitle(main_title="测试标题", sub_title=""),
        format_profile_payload=profile.template_payload,
        format_profile_title_policy=profile.title_policy,
        skip_reference_image_understanding=True,
    )
    result = __import__("asyncio").run(article_agent_service.agent3_generate_content(state))

    assert result.error is None
    assert "新的正文" in result.content
    assert "新的视觉标题" not in result.content
    assert result.title.main_title == "新的公众号标题"


def test_format_profile_visual_title_slot_may_match_wechat_title(monkeypatch) -> None:
    """模板声明的视觉标题可与公众号标题相同，不能被旧正文去重逻辑重写。"""

    import app.services.article_agent_service as article_agent_service
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.format_profile_service import analyze_feed_article_format

    profile = analyze_feed_article_format(
        article_id=45,
        article_title="标题模板",
        body_html='<section><h1>原始标题</h1><p>原始导语</p></section>',
    )

    async def fake_call_llm(*_args, **_kwargs):
        return (
            '{"wechat_title":"同一标题","visual_title":"同一标题",'
            '"visual_subtitle":"","text_slots":[{"id":"text-1","content":"原始标题"},'
            '{"id":"text-2","content":"新的导语"}],"image_slots":[]}'
        )

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    state = ArticleState(
        task_id="profile-title-slot",
        topic="测试主题",
        title=SelectedTitle(main_title="初始标题", sub_title=""),
        format_profile_payload=profile.template_payload,
        format_profile_title_policy=profile.title_policy,
        skip_reference_image_understanding=True,
    )

    result = __import__("asyncio").run(article_agent_service.agent3_generate_content(state))

    assert result.error is None
    assert "同一标题" in result.content
    assert "新的导语" in result.content
