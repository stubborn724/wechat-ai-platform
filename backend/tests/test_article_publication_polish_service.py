"""文章发布统一后处理的回归测试。

这些规则不能依赖每篇投喂源的 Prompt：来源文章会不断变化，而水印、来源联系方式
过滤和 AI 图片说明必须由程序在固定边界保证。
"""

from io import BytesIO
import asyncio
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw


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


def test_article_attribution_font_size_is_small_enough_for_1024px_images():
    """1024px 文章图的动态水印应控制在约 3% 比例，不能再按 6% 放大。"""
    from app.services.article_publication_polish_service import (
        calculate_article_attribution_font_size,
    )

    font_size = calculate_article_attribution_font_size(1024)

    assert 24 <= font_size <= 36
    assert font_size < 1024 * 0.04


def test_article_attribution_accepts_fixed_24px_font_for_scheduled_images():
    """定时 ERP 图片使用固定画布时，水印字号必须可以明确锁定为 24px。"""
    from app.services.article_publication_polish_service import (
        apply_article_image_attribution_to_bytes,
        build_article_image_attribution,
    )

    original_image = Image.new("RGB", (1024, 1365), "#d9d9d9")
    original_buffer = BytesIO()
    original_image.save(original_buffer, format="PNG")

    attributed_bytes = apply_article_image_attribution_to_bytes(
        original_buffer.getvalue(),
        attribution=build_article_image_attribution(
            product_name="异形茶几",
            brand_contact="绣蔓家具TEL:18682130473",
        ),
        content_type="image/png",
        font_size=24,
    )

    assert attributed_bytes


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


def test_apply_article_image_attribution_supports_task_snapshot_bottom_left_position():
    """任务级水印位置必须生效，不能把所有新公众号都固定到右下角。"""
    from app.services.article_publication_polish_service import (
        ArticleImageAttribution,
        apply_article_image_attribution_to_bytes,
    )

    background = (217, 217, 217)
    original_image = Image.new("RGB", (1024, 768), background)
    original_buffer = BytesIO()
    original_image.save(original_buffer, format="PNG")

    attributed_bytes = apply_article_image_attribution_to_bytes(
        original_buffer.getvalue(),
        attribution=ArticleImageAttribution(
            lines=("中西无界 TEL:18138381749",),
            position="bottom-left",
            margin=40,
        ),
        content_type="image/png",
        font_size=24,
    )

    attributed_image = Image.open(BytesIO(attributed_bytes)).convert("RGB")
    assert any(
        attributed_image.getpixel((x, y)) != background
        for x in range(0, 360)
        for y in range(600, 768)
    )
    assert all(
        attributed_image.getpixel((x, y)) == background
        for x in range(600, 1024)
        for y in range(600, 768)
    )


def test_apply_article_image_attribution_keeps_wechat_preview_text_readable():
    """公众号缩放正文图后，水印仍需接近示例的可读比例，而不能只剩细小灰点。

    这里用纯色底图测量实际绘制像素的包围盒：要求文字保持单行、位于底部偏右，
    并占据足够的横向比例。这个断言能捕获字体回退为默认小位图、字号上限过低
    等肉眼难以通过普通单元测试发现的问题。
    """
    from app.services.article_publication_polish_service import (
        apply_article_image_attribution_to_bytes,
        build_article_image_attribution,
    )

    background = (217, 217, 217)
    original_image = Image.new("RGB", (1024, 768), background)
    original_buffer = BytesIO()
    original_image.save(original_buffer, format="PNG")

    attributed_bytes = apply_article_image_attribution_to_bytes(
        original_buffer.getvalue(),
        attribution=build_article_image_attribution(
            product_name="维多利亚餐桌",
            brand_contact="绣蔓家具TEL:18682130473",
        ),
        content_type="image/png",
    )

    attributed_image = Image.open(BytesIO(attributed_bytes)).convert("RGB")
    diff = ImageChops.difference(attributed_image, original_image)
    bbox = diff.getbbox()

    assert bbox is not None
    changed_width = bbox[2] - bbox[0]
    changed_height = bbox[3] - bbox[1]
    assert changed_width >= int(original_image.width * 0.5)
    assert changed_height <= int(original_image.height * 0.15)
    assert bbox[0] >= int(original_image.width * 0.2)
    assert bbox[1] >= int(original_image.height * 0.75)


def test_load_font_fallback_honors_requested_size(monkeypatch):
    """容器暂时缺少字体时，回退字体也不能无视请求字号退化成 10px 默认字。

    生产镜像会提供完整中文字体；这个测试额外约束异常环境的降级行为，避免字体
    路径变化后再次出现“代码设置大字号、图片实际仍很小”的静默回归。
    """
    import app.services.article_publication_polish_service as polish

    monkeypatch.setattr(polish, "_CJK_FONT_PATHS", ())
    font = polish._load_font(60)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = probe.textbbox((0, 0), "绣蔓家具 TEL:18682130473", font=font)

    assert bbox[3] - bbox[1] >= 40


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

    async def fake_download(image_url):
        return f"bytes:{image_url}".encode(), "image/jpeg"

    monkeypatch.setattr(polish, "download_image_bytes", fake_download)
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
async def test_normalize_final_article_images_archives_body_images_concurrently_in_dom_order(monkeypatch):
    """最终正文图片归档应并发等待慢网络，并按 DOM 顺序回填结果。

    图片归档包含下载、压缩、水印和上传，真实环境中每张图都可能等待网络。优化后
    允许并发等待这些 I/O，但正文顺序必须仍以 HTML DOM 为准，避免封面和正文图
    出现错位。
    """

    from app.services import article_publication_polish_service as polish

    active_downloads = 0
    max_active_downloads = 0
    archived_payloads = []
    delays = {
        "https://videos.tpkcur.xyz/slow.png": 0.09,
        "https://videos.tpkcur.xyz/fast.png": 0.03,
        "https://videos.tpkcur.xyz/mid.png": 0.06,
    }

    async def fake_download(image_url):
        nonlocal active_downloads, max_active_downloads
        active_downloads += 1
        max_active_downloads = max(max_active_downloads, active_downloads)
        try:
            await asyncio.sleep(delays[image_url])
            return f"bytes:{image_url}".encode(), "image/png"
        finally:
            active_downloads -= 1

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        archived_payloads.append((image_url, kwargs["image_bytes"], kwargs["image_content_type"]))
        order = {
            "https://videos.tpkcur.xyz/slow.png": "1",
            "https://videos.tpkcur.xyz/fast.png": "2",
            "https://videos.tpkcur.xyz/mid.png": "3",
        }[image_url]
        return SimpleNamespace(storage_key=f"assets/107/{order}.jpg")

    monkeypatch.setattr(polish, "download_image_bytes", fake_download)
    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )

    started_at = time.perf_counter()
    normalized = await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content=(
            '<article>'
            '<img src="https://videos.tpkcur.xyz/slow.png"/>'
            '<img src="https://videos.tpkcur.xyz/fast.png"/>'
            '<img src="https://videos.tpkcur.xyz/mid.png"/>'
            '</article>'
        ),
        tenant_id=107,
        product_name="异形茶几",
    )
    elapsed = time.perf_counter() - started_at

    assert max_active_downloads == 3
    assert elapsed < 0.14
    assert archived_payloads == [
        (
            "https://videos.tpkcur.xyz/slow.png",
            b"bytes:https://videos.tpkcur.xyz/slow.png",
            "image/png",
        ),
        (
            "https://videos.tpkcur.xyz/fast.png",
            b"bytes:https://videos.tpkcur.xyz/fast.png",
            "image/png",
        ),
        (
            "https://videos.tpkcur.xyz/mid.png",
            b"bytes:https://videos.tpkcur.xyz/mid.png",
            "image/png",
        ),
    ]
    assert normalized.body_image_urls == (
        "http://localhost:9002/wechat-assets/assets/107/1.jpg",
        "http://localhost:9002/wechat-assets/assets/107/2.jpg",
        "http://localhost:9002/wechat-assets/assets/107/3.jpg",
    )
    assert normalized.content.index("assets/107/1.jpg") < normalized.content.index("assets/107/2.jpg")
    assert normalized.content.index("assets/107/2.jpg") < normalized.content.index("assets/107/3.jpg")


@pytest.mark.asyncio
async def test_normalize_final_article_images_passes_fixed_size_policy_to_archive(monkeypatch):
    """ERP 定时归档必须把固定画布和 24px 水印策略传到素材归档层。"""
    from app.services import article_publication_polish_service as polish

    captured = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(storage_key="assets/107/fixed.png")

    monkeypatch.setattr(
        polish,
        "download_image_bytes",
        lambda image_url: asyncio.sleep(0, result=(b"image-bytes", "image/jpeg")),
    )
    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )

    await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content='<article><img src="https://videos.tpkcur.xyz/a.png"/></article>',
        tenant_id=107,
        product_name="异形茶几",
        target_size=(1024, 1365),
        watermark_font_size=24,
    )

    assert captured[0]["target_size"] == (1024, 1365)
    assert captured[0]["watermark_font_size"] == 24


@pytest.mark.asyncio
async def test_normalize_final_article_images_passes_task_watermark_switch_to_archive(monkeypatch):
    """定时任务的水印开关必须传到归档层，避免界面勾选与实际图片不一致。"""
    from app.services import article_publication_polish_service as polish

    captured = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(storage_key="assets/107/no-global-watermark.jpg")

    monkeypatch.setattr(
        polish,
        "download_image_bytes",
        lambda image_url: asyncio.sleep(0, result=(b"image-bytes", "image/jpeg")),
    )
    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )

    await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content='<article><img src="https://videos.tpkcur.xyz/a.png"/></article>',
        tenant_id=107,
        product_name="异形茶几",
        watermark_enabled=False,
    )

    assert captured[0]["watermark_enabled"] is False
    assert captured[0]["article_image_attribution"] is None


@pytest.mark.asyncio
async def test_normalize_seamless_poster_archives_three_slices_from_three_views(monkeypatch):
    """连续海报应下载三种机位，合成后仍按 HTML 图片顺序逐张归档。"""
    from app.services import article_publication_polish_service as polish

    captured = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        captured.append((image_url, kwargs))
        return SimpleNamespace(storage_key=f"assets/107/poster-{len(captured)}.png")

    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )
    monkeypatch.setattr(
        polish,
        "build_continuous_poster_slices",
        lambda *_args, **_kwargs: (b"slice-1", b"slice-2", b"slice-3"),
    )
    async def fake_download(url, *_args, **_kwargs):
        return f"image:{url}".encode(), "image/png"

    monkeypatch.setattr(polish, "download_image_bytes", fake_download)

    normalized = await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content=(
            '<div data-ai-layout="seamless-poster">'
            '<img src="https://provider.example.com/view-1.png" data-poster-copy="第一段" data-poster-kind="title"/>'
            '<img src="https://provider.example.com/view-2.png" data-poster-copy="第二段" data-poster-kind="content"/>'
            '<img src="https://provider.example.com/view-3.png" data-poster-copy="第三段" data-poster-kind="content"/>'
            '</div>'
        ),
        tenant_id=107,
        product_name="现代餐桌",
    )

    assert [item[0] for item in captured] == [
        "https://provider.example.com/view-1.png",
        "https://provider.example.com/view-2.png",
        "https://provider.example.com/view-3.png",
    ]
    assert [item[1]["image_bytes"] for item in captured] == [b"slice-1", b"slice-2", b"slice-3"]
    assert [item[1]["poster_copy"] for item in captured] == [None, None, None]
    # 三张切片只是同一幅海报的文件载体，水印只落最后一段，避免上一张底部的
    # 联系方式在下一张顶部紧贴出现，破坏连续阅读。
    assert [item[1]["watermark_enabled"] for item in captured] == [False, False, None]
    assert [item[1]["article_image_attribution"] for item in captured[:2]] == [None, None]
    assert normalized.body_image_urls == (
        "http://localhost:9002/wechat-assets/assets/107/poster-1.png",
        "http://localhost:9002/wechat-assets/assets/107/poster-2.png",
        "http://localhost:9002/wechat-assets/assets/107/poster-3.png",
    )


@pytest.mark.asyncio
async def test_normalize_final_article_images_refuses_to_publish_when_a_body_image_cannot_be_archived(monkeypatch):
    """直接发布不能吞掉归档失败，否则会重新出现一篇文章混用有无署名图片。"""
    from app.services import article_publication_polish_service as polish

    async def failing_archive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        polish,
        "download_image_bytes",
        lambda image_url: asyncio.sleep(0, result=(b"image-bytes", "image/jpeg")),
    )
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
