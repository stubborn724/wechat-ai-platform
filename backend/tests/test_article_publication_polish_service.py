"""文章发布统一后处理的回归测试。

这些规则不能依赖每篇投喂源的 Prompt：来源文章会不断变化，而水印、来源联系方式
过滤和 AI 图片说明必须由程序在固定边界保证。
"""

from io import BytesIO
import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证纯内容转换，覆盖项目级 MySQL 清理夹具。"""
    yield


def test_build_article_image_attribution_uses_single_normalized_brand_contact():
    """正文图统一使用示例中的单行品牌电话，不能再绘制产品名和双行底栏。"""
    from app.services.article_publication_polish_service import build_article_image_attribution

    attribution = build_article_image_attribution(
        product_name="FSJJ-20241020116 双层圆形边几",
        brand_contact="绣蔓家具TEL:18682130473",
    )

    assert attribution.lines == ("绣蔓家具 TEL:18682130473",)


def test_apply_article_image_attribution_places_single_line_at_bottom_right_without_footer_band():
    """水印应只出现在右下角，不能再覆盖整条图片底部。"""
    from app.services.article_publication_polish_service import (
        apply_article_image_attribution_to_bytes,
        build_article_image_attribution,
    )

    original_image = Image.new("RGB", (640, 480), "#d9d9d9")
    original_buffer = BytesIO()
    original_image.save(original_buffer, format="PNG")
    original_bytes = original_buffer.getvalue()

    attributed_bytes = apply_article_image_attribution_to_bytes(
        original_bytes,
        attribution=build_article_image_attribution(
            product_name="维多利亚餐桌",
            brand_contact="绣蔓家具TEL:18682130473",
        ),
        content_type="image/png",
    )

    attributed_image = Image.open(BytesIO(attributed_bytes)).convert("RGB")
    assert attributed_bytes != original_bytes
    # 左下角不属于水印区域，必须保持原色；右下角文字区域则会实际改变像素。
    assert attributed_image.getpixel((10, 470)) == (217, 217, 217)
    # 字体渲染会因 Windows 字体度量产生少量坐标差异，因此验证整个右下区域存在
    # 实际绘制像素，而不是把测试绑定到某个具体字形的单一像素。
    assert any(
        attributed_image.getpixel((x, y)) != (217, 217, 217)
        for x in range(400, 640)
        for y in range(400, 480)
    )


def test_apply_article_image_attribution_handles_narrow_images_without_truncation_loop():
    """异常窄图也必须快速返回，产品长名称不能让定时任务卡在截断循环中。"""
    from app.services.article_publication_polish_service import (
        apply_article_image_attribution_to_bytes,
        build_article_image_attribution,
    )

    original = Image.new("RGB", (40, 80), "#d9d9d9")
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    result = apply_article_image_attribution_to_bytes(
        buffer.getvalue(),
        attribution=build_article_image_attribution(
            product_name="FSJJ-20241020116 双层圆形边几",
            brand_contact="绣蔓家具TEL:18682130473",
        ),
        content_type="image/png",
    )

    assert result


def test_append_ai_image_disclaimer_appends_once_after_existing_footer():
    """AI 图片说明必须放在文章末尾，并且多次后处理也只能保留一份。"""
    from app.services.article_publication_polish_service import append_ai_image_disclaimer

    initial_content = (
        '<article><p>正文内容</p></article>'
        '<div data-ai-footer-template="appended"><p>绣蔓家具TEL:18682130473</p></div>'
    )

    once = append_ai_image_disclaimer(initial_content)
    twice = append_ai_image_disclaimer(once)

    assert once.index("绣蔓家具TEL:18682130473") < once.index("部分图片AI生成，具体以实际产品为准。")
    assert twice.count("部分图片AI生成，具体以实际产品为准。") == 1
    assert 'data-ai-image-disclaimer="appended"' in twice


def test_replace_article_image_urls_replaces_archived_urls_in_html_content():
    """归档后的署名图地址必须回填正文，不能继续引用模型的临时图片 URL。"""
    from app.services.article_publication_polish_service import replace_article_image_urls

    result = replace_article_image_urls(
        '<p>正文</p><img src="https://provider.example.com/generated.png" />',
        {"https://provider.example.com/generated.png": "http://localhost:9002/wechat-assets/assets/1/attributed.png"},
    )

    assert "provider.example.com" not in result
    assert "assets/1/attributed.png" in result


def test_trusted_openai_image_delivery_host_can_be_archived_in_current_network():
    """审核过的图片交付域名即使解析到基准网段也必须可进入归档署名流程。"""
    from app.services.url_safety import validate_url

    validate_url("https://videos.tpkcur.xyz/images/generated.png")


def test_archive_image_urls_with_attribution_returns_archived_urls_in_source_order(monkeypatch):
    """纯图片任务也必须使用带署名的归档图，且维持原有图片顺序。"""
    from app.services import asset_archive_service, storage_service
    from app.services.article_publication_polish_service import archive_image_urls_with_attribution

    archived_calls = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        archived_calls.append((tenant_id, image_url, kwargs["original_filename"]))
        return SimpleNamespace(storage_key=f"assets/{tenant_id}/{len(archived_calls)}.png")

    monkeypatch.setattr(asset_archive_service, "save_image_to_asset_library", fake_archive)
    monkeypatch.setattr(
        storage_service.storage_service,
        "get_url",
        lambda storage_key: f"http://localhost:9002/wechat-assets/{storage_key}",
    )

    result = asyncio.run(
        archive_image_urls_with_attribution(
            SimpleNamespace(),
            ["https://provider.example.com/1.png", "https://provider.example.com/2.png"],
            tenant_id=8,
            product_name="维多利亚餐桌",
        )
    )

    assert result == [
        "http://localhost:9002/wechat-assets/assets/8/1.png",
        "http://localhost:9002/wechat-assets/assets/8/2.png",
    ]
    assert archived_calls == [
        (8, "https://provider.example.com/1.png", "维多利亚餐桌"),
        (8, "https://provider.example.com/2.png", "维多利亚餐桌"),
    ]


@pytest.mark.asyncio
async def test_normalize_final_article_images_archives_every_body_image_but_keeps_footer_qrcode(monkeypatch):
    """发布前必须覆盖最终 HTML 的全部正文图，固定联系方式二维码不能被产品署名覆盖。"""
    from app.services import article_publication_polish_service as polish

    archived_urls = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        archived_urls.append(image_url)
        assert kwargs["article_image_attribution"].lines[0] == "绣蔓家具 TEL:18682130473"
        return SimpleNamespace(storage_key=f"assets/107/{len(archived_urls)}.jpg")

    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )

    content = (
        '<article><img src="https://videos.tpkcur.xyz/a.png"/>'
        '<img src="https://videos.tpkcur.xyz/b.png"/></article>'
        '<div data-ai-footer-template="appended"><img alt="二维码" '
        'src="http://localhost:9002/wechat-assets/qr.png"/></div>'
    )

    normalized = await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content=content,
        tenant_id=107,
        product_name="ZH-05 长方形餐桌",
    )

    assert archived_urls == [
        "https://videos.tpkcur.xyz/a.png",
        "https://videos.tpkcur.xyz/b.png",
    ]
    assert "assets/107/1.jpg" in normalized.content
    assert "assets/107/2.jpg" in normalized.content
    assert 'src="http://localhost:9002/wechat-assets/qr.png"' in normalized.content
    assert normalized.body_image_urls == (
        "http://localhost:9002/wechat-assets/assets/107/1.jpg",
        "http://localhost:9002/wechat-assets/assets/107/2.jpg",
    )


@pytest.mark.asyncio
async def test_normalize_final_article_images_refuses_to_publish_when_a_body_image_cannot_be_archived(monkeypatch):
    """直接发布不能吞掉归档失败，否则会重新出现一篇文章混用有无署名图片。"""
    from app.services import article_publication_polish_service as polish

    async def failing_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        failing_archive,
    )

    with pytest.raises(polish.ArticleImageNormalizationError, match="无法归档"):
        await polish.normalize_final_article_images_with_attribution(
            db=SimpleNamespace(),
            content='<article><img src="https://videos.tpkcur.xyz/a.png"/></article>',
            tenant_id=107,
            product_name="ZH-05 长方形餐桌",
        )
