"""遗留 LangGraph 图片生成节点的单元测试。

该节点仍可能被旧调用方使用，因此必须确保其不会丢弃上游已合成的视觉提示词，
避免退回只按关键词生成图片。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """节点测试只验证内存对象，不依赖本地数据库。"""
    yield


def test_resolve_image_single_passes_requirement_prompt_to_strategy(monkeypatch):
    """图片策略必须收到完整提示词，而不是只有图片关键词。"""
    from app.agent.nodes import image_generation_node
    from app.schemas.article import ImageRequirement

    class FakeStrategy:
        received_method = ""
        received_keywords = ""
        received_prompt = ""

        async def execute(self, method, keywords, *, prompt):
            type(self).received_method = method
            type(self).received_keywords = keywords
            type(self).received_prompt = prompt
            return "https://generated.example/image.png"

    monkeypatch.setattr(image_generation_node, "ImageServiceStrategy", FakeStrategy, raising=False)
    requirement = ImageRequirement(
        position=1,
        type="inline",
        image_source="DASHSCOPE",
        keywords="新主体",
        prompt="结构化视觉提示词",
        placeholder_id="image-1",
    )

    result = image_generation_node._resolve_image_single(requirement.model_dump(), "DASHSCOPE")

    assert result is not None
    assert result.url == "https://generated.example/image.png"
    assert FakeStrategy.received_method == "DASHSCOPE"
    assert FakeStrategy.received_keywords == "新主体"
    assert FakeStrategy.received_prompt == "结构化视觉提示词"


@pytest.mark.asyncio
async def test_reference_generation_fails_when_required_image_is_missing(monkeypatch):
    """图生图返回空地址时必须终止流程，不能生成少图文章。"""
    from app.services import article_agent_service
    from app.services import image_generation_service as service_module
    from app.services.image_generation_models import ImageErrorCategory, ImageProviderError
    from app.schemas.article import ArticleState, ImageRequirement

    async def fail_generation(*args, **kwargs):
        """模拟主备路由均无法返回必需图片的异常边界。"""
        raise ImageProviderError(
            "图片结果为空",
            category=ImageErrorCategory.EMPTY_RESULT,
            provider="openai_compatible",
        )

    monkeypatch.setattr(service_module.image_generation_service, "generate", fail_generation)
    state = ArticleState(
        task_id="task-1",
        topic="家具",
        reference_image_url="https://cos.example.com/reference.jpg",
        image_requirements=[ImageRequirement(
            position=1,
            type="inline",
            keywords="子母茶几",
            prompt="保留家具主体，替换为客厅背景",
            image_source="DASHSCOPE",
        )],
    )

    with pytest.raises(ImageProviderError, match="图片结果为空"):
        await article_agent_service.agent5_generate_images(state)
