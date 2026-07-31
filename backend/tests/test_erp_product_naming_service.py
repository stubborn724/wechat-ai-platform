"""ERP 产品展示名补全服务测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """命名服务为纯函数测试，不访问本地业务数据库。"""
    yield


@pytest.mark.asyncio
async def test_product_code_is_enriched_with_visual_chinese_description() -> None:
    """纯 ERP 编号必须拼接视觉 Agent 返回的保守中文品类说明。"""
    from app.services.erp_product_naming_service import enrich_erp_product_display_name

    captured = {}

    async def fake_analyzer(image_url: str) -> str:
        captured["image_url"] = image_url
        return "双层圆形边几"

    display_name = await enrich_erp_product_display_name(
        product_name="FSJJ-20241020116",
        image_url="https://cos.example.com/erp-product.jpg",
        analyze_image=fake_analyzer,
    )

    assert display_name == "FSJJ-20241020116 双层圆形边几"
    assert captured["image_url"] == "https://cos.example.com/erp-product.jpg"


@pytest.mark.asyncio
async def test_product_code_uses_safe_fallback_when_visual_analysis_fails() -> None:
    """视觉模型不可用不能阻断定时发布，仍需返回可读的稳定产品名。"""
    from app.services.erp_product_naming_service import enrich_erp_product_display_name

    async def failing_analyzer(_image_url: str) -> str:
        raise RuntimeError("vision unavailable")

    display_name = await enrich_erp_product_display_name(
        product_name="FSJJ-20241020116",
        image_url="https://cos.example.com/erp-product.jpg",
        analyze_image=failing_analyzer,
    )

    assert display_name == "FSJJ-20241020116 家具单品"


@pytest.mark.asyncio
async def test_existing_chinese_product_name_does_not_spend_extra_visual_call() -> None:
    """ERP 已提供中文名称时保留原值，避免无意义消耗视觉模型额度。"""
    from app.services.erp_product_naming_service import enrich_erp_product_display_name

    async def unexpected_analyzer(_image_url: str) -> str:
        raise AssertionError("中文 ERP 名称不应调用视觉 Agent")

    display_name = await enrich_erp_product_display_name(
        product_name="维多利亚餐桌",
        image_url="https://cos.example.com/erp-product.jpg",
        analyze_image=unexpected_analyzer,
    )

    assert display_name == "维多利亚餐桌"
