"""文章 Agent 图片生成统一路由测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """图片 Agent 路由测试不访问业务数据库。"""
    yield


@pytest.mark.asyncio
async def test_erp_reference_generation_uses_shared_provider_request(monkeypatch):
    """ERP 图生图必须把参考字节与万相降级 URL 一并交给统一服务。"""
    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_generation_models import GeneratedImage
    from app.services import image_generation_service as service_module

    captured_requests = []

    async def fake_generate(request):
        captured_requests.append(request)
        return GeneratedImage(
            url="http://localhost:9002/wechat-assets/generated-images/107/result.png",
            provider="openai_compatible",
            model="gpt-image-2",
        )

    monkeypatch.setattr(service_module.image_generation_service, "generate", fake_generate)
    state = ArticleState(
        task_id="scheduled-1",
        tenant_id=107,
        topic="云朵茶几",
        product_name="云朵茶几",
        image_prompt_context="品牌色调克制，现代客厅场景",
        reference_image_url="https://cos.example.com/signed-reference",
        reference_image_bytes=b"normalized-product-image",
        reference_content_type="image/jpeg",
        image_requirements=[ImageRequirement(
            position=1,
            type="inline",
            keywords="云朵茶几",
            prompt="保留茶几主体，仅替换客厅背景",
            image_source="DASHSCOPE",
            placeholder_id="image-1",
        )],
    )

    result = await agent5_generate_images(state)

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.tenant_id == 107
    assert request.reference_image_bytes == b"normalized-product-image"
    assert request.reference_content_type == "image/jpeg"
    assert request.reference_image_url == "https://cos.example.com/signed-reference"
    assert "品牌色调克制" in request.prompt
    assert "目标产品：云朵茶几" in request.prompt
    assert result.images[0].method == "openai_compatible"


@pytest.mark.asyncio
async def test_erp_reference_generation_caps_cost_at_five_images(monkeypatch):
    """即使上游产生更多槽位，ERP 图生图每篇也最多调用五次模型。"""

    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_generation_models import GeneratedImage
    from app.services import image_generation_service as service_module

    captured_requests = []

    async def fake_generate(request):
        """记录模型调用次数，返回稳定的归档图片地址。"""

        captured_requests.append(request)
        return GeneratedImage(
            url=f"http://localhost:9002/generated/{len(captured_requests)}.png",
            provider="openai_compatible",
            model="gpt-image-2",
        )

    monkeypatch.setattr(service_module.image_generation_service, "generate", fake_generate)
    state = ArticleState(
        task_id="scheduled-cost-limit",
        tenant_id=107,
        topic="云朵茶几",
        product_name="云朵茶几",
        image_prompt_context="现代客厅背景",
        reference_image_url="https://cos.example.com/reference",
        reference_image_bytes=b"product-image",
        reference_content_type="image/jpeg",
        image_requirements=[
            ImageRequirement(
                position=index,
                type="inline",
                keywords=f"场景{index}",
                prompt=f"场景{index}",
                image_source="DASHSCOPE",
                placeholder_id=f"image-{index}",
            )
            for index in range(1, 7)
        ],
    )

    result = await agent5_generate_images(state)

    assert len(captured_requests) == 5
    assert len(result.images) == 5
    assert [image.placeholder_id for image in result.images] == [
        "image-1",
        "image-2",
        "image-3",
        "image-4",
        "image-5",
    ]
