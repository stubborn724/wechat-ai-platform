"""文章 Agent 图片生成统一路由测试。"""

import asyncio
import re
import time

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


@pytest.mark.asyncio
async def test_erp_reference_generation_runs_with_bounded_parallelism_and_keeps_slot_order(monkeypatch):
    """ERP 图生图应按受控并发执行，同时保持正文槽位顺序稳定。

    文章发布依赖 ``placeholder_id`` 原位回填 HTML。并发优化不能把先完成的图片
    直接追加到结果尾部，否则会出现图片错位；这里用不同延时模拟真实上游响应，
    验证总耗时接近两批并发而不是三张完全串行。
    """

    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_generation_models import GeneratedImage
    from app.services import image_generation_service as service_module
    from app.services import scheduled_image_quality_service as quality_module

    call_order = []
    active_calls = 0
    max_active_calls = 0
    delays_by_position = {1: 0.09, 2: 0.03, 3: 0.06}

    async def fake_generate(request):
        nonlocal active_calls, max_active_calls
        marker_match = re.search(r"场景(\d+)", request.prompt)
        assert marker_match is not None
        marker = int(marker_match.group(1))
        call_order.append(marker)
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            await asyncio.sleep(delays_by_position[marker])
            return GeneratedImage(
                url=f"http://localhost:9002/generated/{marker}.png",
                provider="openai_compatible",
                model="gpt-image-2",
            )
        finally:
            active_calls -= 1

    async def fake_quality_check(_url):
        return quality_module.ImageQualityReport(True, "测试图片可用")

    monkeypatch.setattr(service_module.image_generation_service, "generate", fake_generate)
    monkeypatch.setattr(
        "app.services.article_agent_service.inspect_generated_image_url",
        fake_quality_check,
    )

    state = ArticleState(
        task_id="scheduled-parallel",
        tenant_id=107,
        topic="云朵茶几",
        product_name="云朵茶几",
        reference_image_url="https://cos.example.com/reference",
        reference_image_bytes=b"product-image",
        reference_content_type="image/jpeg",
        skip_reference_image_understanding=True,
        image_requirements=[
            ImageRequirement(
                position=index,
                type="inline",
                keywords=f"场景{index}",
                prompt=f"场景{index}",
                image_source="DASHSCOPE",
                placeholder_id=f"image-{index}",
            )
            for index in range(1, 4)
        ],
    )

    started_at = time.perf_counter()
    result = await agent5_generate_images(state)
    elapsed = time.perf_counter() - started_at

    assert max_active_calls == 2
    assert elapsed < 0.14
    assert call_order[:2] == [1, 2]
    assert [image.placeholder_id for image in result.images] == [
        "image-1",
        "image-2",
        "image-3",
    ]
    assert [image.url for image in result.images] == [
        "http://localhost:9002/generated/1.png",
        "http://localhost:9002/generated/2.png",
        "http://localhost:9002/generated/3.png",
    ]
