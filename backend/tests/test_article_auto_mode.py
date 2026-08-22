"""文章生成只允许全自动模式的接口契约测试。"""

import asyncio

import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from app.api.v1.articles import (
    CreateArticleRequest,
    _require_generated_content,
    _require_publishable_content,
    _strip_photography_text,
    router,
)
from app.schemas.article import ArticleState
from app.services import article_agent_service
from app.services.article_service import _strip_photography_lines


@pytest.fixture(autouse=True)
def reset_test_tables():
    """请求模型测试不需要数据库，覆盖全局数据库夹具。"""
    yield


def test_create_article_request_defaults_to_auto_mode():
    """未传模式时也必须进入全自动生成链路。"""
    request = CreateArticleRequest(topic="测试主题")

    assert request.mode == "auto"


def test_create_article_request_keeps_selected_cover_separate_from_body_images():
    """手动选择的本地或 ERP 图片必须进入封面字段，不能误作正文配图。"""
    request = CreateArticleRequest(
        topic="测试主题",
        selected_cover_image_url="https://assets.example.com/cover.jpg",
    )

    assert request.selected_cover_image_url == "https://assets.example.com/cover.jpg"
    assert request.selected_image_urls is None


def test_generated_body_image_can_be_used_as_cover_when_ai_cover_is_missing():
    """独立 AI 封面失败时，已生成的正文图片必须成为真实的封面候选。

    文章发布协议要求封面存在。生成阶段不能只记录一条“将使用正文配图”的日志，
    却把空值继续带到发布阶段；这个测试固定封面回退策略，避免正文成功、发布才
    发现封面为空的长链路失败。
    """
    from app.api.v1.articles import select_fallback_cover_image_url

    images = [
        SimpleNamespace(url="https://assets.example.com/body-1.png"),
        SimpleNamespace(url="https://assets.example.com/body-2.png"),
    ]

    assert select_fallback_cover_image_url("", images) == images[0].url
    assert select_fallback_cover_image_url(
        "https://assets.example.com/explicit-cover.png", images
    ) == "https://assets.example.com/explicit-cover.png"


def test_generated_body_image_does_not_create_a_fake_cover_when_none_exists():
    """没有任何真实图片时不得伪造封面地址，调用方应继续走明确失败分支。"""
    from app.api.v1.articles import select_fallback_cover_image_url

    assert select_fallback_cover_image_url("", []) is None
    assert select_fallback_cover_image_url("", [SimpleNamespace(url="")]) is None


def test_article_cover_requirement_explains_why_generation_cannot_be_published():
    """正式文章没有封面时应暴露可行动错误，而不是继续伪装成生成成功。"""
    from app.api.v1.articles import require_article_cover_image_url

    with pytest.raises(ValueError, match="封面图生成失败"):
        require_article_cover_image_url("")

    assert require_article_cover_image_url("https://assets.example.com/cover.png") == (
        "https://assets.example.com/cover.png"
    )


def test_create_article_request_rejects_removed_manual_mode():
    """旧客户端不能再请求需要人工选择标题和大纲的模式。"""
    with pytest.raises(ValidationError):
        CreateArticleRequest(topic="测试主题", mode="manual")


def test_manual_collaboration_routes_are_not_exposed():
    """全自动链路不应保留标题确认和大纲确认接口，避免旧客户端误触发。"""
    paths = {route.path for route in router.routes}

    assert "/articles/{task_id}/confirm-title" not in paths
    assert "/articles/{task_id}/confirm-outline" not in paths
    assert "/articles/{task_id}/ai-modify-outline" not in paths


def test_imitation_title_agent_generates_new_title_for_empty_topic(monkeypatch):
    """空主题的投喂仿写必须生成新标题，不能直接沿用参考文章标题。"""
    async def fake_call_llm(system_message, prompt, temperature=0.7):
        assert "参考文章标题" in prompt
        return '{"title_options":[{"main_title":"重写后的原创标题","sub_title":"新的补充说明"}]}'

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    state = ArticleState(
        task_id="test-imitation-title",
        topic="",
        reference_articles=["## 参考文章标题\n\n参考文章正文内容"],
    )

    result = asyncio.run(
        article_agent_service.agent1_generate_imitation_title(state, "参考文章标题")
    )

    assert result.error is None
    assert result.title_options[0].main_title == "重写后的原创标题"
    assert result.title_options[0].main_title != "参考文章标题"


def test_imitation_title_agent_rejects_reference_title_as_result(monkeypatch):
    """模型复用了原题时必须将其视为失败，避免发布成原文标题。"""
    async def fake_call_llm(system_message, prompt, temperature=0.7):
        return '{"title_options":[{"main_title":"参考文章标题","sub_title":""}]}'

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    state = ArticleState(task_id="test-reference-title", topic="")

    result = asyncio.run(
        article_agent_service.agent1_generate_imitation_title(state, "参考文章标题")
    )

    assert result.title_options == []
    assert "不能与参考原标题相同" in (result.error or "")


def test_empty_generated_content_is_rejected_before_publish():
    """正文 Agent 无内容时必须中断，不能将空 HTML 发送给微信中转站。"""
    with pytest.raises(ValueError, match="正文生成失败"):
        _require_generated_content("", "模型返回内容为空")


def test_generated_html_allows_image_slots_before_image_agent_runs():
    """HTML 正文在图片 Agent 回填前允许保留图片槽位。"""
    content = '<img src="__AI_IMAGE_SLOT_image-1__" />'

    assert _require_generated_content(content) == content


def test_unreplaced_html_image_slot_is_rejected_before_publish():
    """HTML 图片槽位未回填时，不能将占位符作为图片地址提交给中转站。"""
    with pytest.raises(ValueError, match="图片占位符未替换"):
        _require_publishable_content('<img src="__AI_IMAGE_SLOT_image-1__" />')


def test_html_content_is_not_deleted_by_photography_text_cleanup():
    """HTML 仿写正文即使含摄影词，也不能被按整行文本误删。"""
    html = (
        '<section><p>暖光下的客厅特写，让空间层次更清晰。</p>'
        '<img src="https://assets.example.com/content.png" alt="客厅特写" /></section>'
    )

    assert _strip_photography_lines(html) == html


def test_api_html_cleanup_keeps_html_imitation_content():
    """API 层的兼容清理同样不得删除整行 HTML 仿写正文。"""
    html = (
        '<section><p>暖光下的客厅特写，让空间层次更清晰。</p>'
        '<img src="https://assets.example.com/content.png" alt="客厅特写" /></section>'
    )

    assert _strip_photography_text(html) == html
