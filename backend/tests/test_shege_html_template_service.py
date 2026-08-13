"""她格 HTML 结构化图文模板的契约测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """模板构造为纯函数，不连接业务数据库。"""

    yield


def test_shege_template_has_fixed_section_and_image_slots_for_html_rendering():
    """模板应把图片固定在段间节点，而不是依赖 Markdown 标题猜测位置。"""

    from app.services.shege_html_template_service import build_shege_html_template
    from app.services.format_profile_service import analyze_feed_article_format

    html = build_shege_html_template()
    profile = analyze_feed_article_format(
        article_id=801,
        article_title="她格原创图文版式",
        body_html=html,
    )

    assert profile.render_mode == "html_slots"
    assert len(profile.template_payload["blueprint"]["image_slots"]) == 4
    assert profile.title_policy["visual_title_slot_id"] == "text-2"
    assert "data-shege-layout" in html


def test_shege_template_task_patch_binds_only_the_explicit_template():
    """她格任务绑定模板时必须冻结投喂源和格式版本，不能开启自动切换。"""

    from app.services.shege_html_template_service import build_shege_template_task_patch

    patch = build_shege_template_task_patch(
        feed_source_id=51,
        feed_article_id=61,
        format_profile_id=71,
    )

    assert patch["writing_mode"] == "feed"
    assert patch["feed_source_id"] == 51
    assert patch["feed_source_ids"] == [51]
    assert patch["feed_article_ids"] == [61]
    assert patch["format_profile_id"] == 71
    assert patch["format_profile_auto_bind_enabled"] is False
    assert patch["html_image_count"] == 4


def test_shege_html_generation_prompt_keeps_the_reusable_writing_style():
    """HTML 模板任务也必须使用她格标题与正文规则，不能退化为通用仿写。"""

    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_agent_service import _build_html_imitation_prompt
    from app.services.format_profile_service import analyze_feed_article_format
    from app.services.shege_html_template_service import build_shege_html_template

    profile = analyze_feed_article_format(
        article_id=802,
        article_title="她格原创图文版式",
        body_html=build_shege_html_template(),
    )
    state = ArticleState(
        task_id="shege-template-contract",
        topic="",
        style="shege_enterprise_ai_service",
        title=SelectedTitle(main_title="公众号文章", sub_title=""),
        kb_context="她格知识库内容",
        image_prompt_context="她格图片规则",
        format_profile_payload=profile.template_payload,
        format_profile_title_policy=profile.title_policy,
    )

    prompt = _build_html_imitation_prompt(state, profile.template_payload, {})

    assert "她格企业 AI 服务写作要求" in prompt
    assert "完整长句" in prompt
