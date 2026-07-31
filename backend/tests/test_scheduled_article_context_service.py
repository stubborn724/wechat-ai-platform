"""ERP 定时文章上下文编排的回归测试。

这些测试只验证纯上下文合成逻辑，不连接 MySQL、PostgreSQL 或外部 ERP，避免
在本地真实业务库上运行测试时产生清表或素材占用副作用。
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖项目级清表夹具，本测试不需要也不允许访问业务数据库。"""

    yield


def test_knowledge_context_contains_all_assigned_brand_rules() -> None:
    """品牌背景规则应按知识库顺序完整合成，不能因检索主题为空而消失。"""

    from app.services.scheduled_article_context_service import compose_knowledge_context

    chunks = [
        SimpleNamespace(id=11, knowledge_base_id=1, content="背景使用克制的现代客厅，保留自然留白。"),
        SimpleNamespace(id=12, knowledge_base_id=1, content="整体采用柔和中性色，不加入文字和品牌标识。"),
    ]

    context = compose_knowledge_context(chunks)

    assert "现代客厅" in context
    assert "柔和中性色" in context
    assert "chunk_id=11" in context
    assert "chunk_id=12" in context


def test_knowledge_context_splits_article_format_from_image_background() -> None:
    """文章 Agent 与图片 Agent 不应收到彼此无关的知识库章节。"""

    from app.services.scheduled_article_context_service import split_knowledge_prompt_context

    contexts = split_knowledge_prompt_context("""【文章形式】沿用投喂源的图文结构，不新增独立模块。
【文案要求】正文克制、有文化感，不写价格。
【品牌调性】中西融合，贵气内敛。
【图片要求】背景使用墨绿、古铜金与低饱和家居场景，保留产品主体。""")

    assert "沿用投喂源" in contexts.article_context
    assert "正文克制" in contexts.article_context
    assert "墨绿" not in contexts.article_context
    assert "墨绿" in contexts.image_context
    assert "保留产品主体" in contexts.image_context
    assert "文章形式" not in contexts.image_context


def test_product_context_is_shared_by_article_and_image_agents() -> None:
    """产品名共享，但文章格式与图片背景必须注入不同状态字段。"""

    from app.schemas.article import ArticleState
    from app.services.scheduled_article_context_service import bind_product_context

    state = ArticleState(task_id="scheduled-1", tenant_id=107, topic="")

    bind_product_context(
        state=state,
        product_name="铜脚沙发",
        configured_topic="",
        article_context="正文沿用投喂源的分段与克制语气。",
        image_context="背景采用安静的现代客厅，光影自然。",
    )

    assert state.product_name == "铜脚沙发"
    assert "铜脚沙发" in state.topic
    assert "铜脚沙发" in (state.user_description or "")
    assert "投喂源" in (state.kb_context or "")
    assert "现代客厅" not in (state.kb_context or "")
    assert "现代客厅" in (state.image_prompt_context or "")
    assert state.image_prompt_context != state.kb_context


def test_feed_imitation_allows_background_only_knowledge_context() -> None:
    """投喂源已提供文章结构时，只绑定背景说明仍可驱动 ERP 图生图。"""

    from app.schemas.article import ArticleState
    from app.services.scheduled_article_context_service import bind_product_context

    state = ArticleState(task_id="scheduled-feed", tenant_id=107, topic="")

    bind_product_context(
        state=state,
        product_name="异形子母茶几",
        configured_topic="",
        article_context="",
        image_context="背景采用暖色现代客厅，保持产品主体清晰。",
        require_article_context=False,
    )

    assert state.product_name == "异形子母茶几"
    assert state.kb_context == ""
    assert "暖色现代客厅" in (state.image_prompt_context or "")


def test_selected_title_always_contains_product_name() -> None:
    """模型遗漏产品名时由程序稳定补齐，保证最终发布标题满足业务规则。"""

    from app.schemas.article import SelectedTitle
    from app.services.scheduled_article_context_service import ensure_product_name_in_title

    selected = SelectedTitle(main_title="未来的客厅，正在告别标准件", sub_title="从空间关系重新理解家具")

    result = ensure_product_name_in_title(selected, "铜脚沙发")

    assert result.main_title == "铜脚沙发｜未来的客厅，正在告别标准件"
    assert result.sub_title == selected.sub_title


def test_selected_title_does_not_repeat_existing_product_name() -> None:
    """模型已自然写入产品名时保持原标题，避免程序产生重复前缀。"""

    from app.schemas.article import SelectedTitle
    from app.services.scheduled_article_context_service import ensure_product_name_in_title

    selected = SelectedTitle(main_title="铜脚沙发，让客厅重新松弛下来", sub_title="材质与空间的自然关系")

    result = ensure_product_name_in_title(selected, "铜脚沙发")

    assert result.main_title == selected.main_title


@pytest.mark.asyncio
async def test_standard_title_agent_receives_required_product_name(monkeypatch) -> None:
    """定时任务实际调用的通用标题 Agent 必须收到产品名强制规则。"""

    from app.schemas.article import ArticleState
    from app.services import article_agent_service as service_module

    captured = {}

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        """截获实际 Prompt，并返回一组结构合法的标题结果。"""

        captured["prompt"] = user_message
        return '{"title_options":[{"main_title":"铜脚沙发，让客厅更松弛","sub_title":"空间关系的新答案"}]}'

    monkeypatch.setattr(service_module, "_call_llm", fake_call_llm)
    state = ArticleState(
        task_id="scheduled-title",
        tenant_id=107,
        topic="铜脚沙发",
        product_name="铜脚沙发",
    )

    await service_module.agent1_generate_title_options(state)

    assert "产品名称：铜脚沙发" in captured["prompt"]
    assert "主标题都必须原样包含" in captured["prompt"]
