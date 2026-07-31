"""参考图片分析服务的单元测试。

这些测试保证图文与纯图片流程使用同一套二维码过滤规则，且模型返回顺序异常时
不会把某张图片的视觉描述错误套到另一张参考图上。
"""

import pytest

from app.services.reference_media_analysis_service import (
    analyze_markdown_reference_images,
    analyze_reference_images,
    extract_markdown_image_urls,
)


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具，纯函数测试不应被本地数据库外键状态影响。"""
    yield


def test_extract_markdown_image_urls_preserves_source_order():
    """Markdown 图片提取必须保留文章中的原始顺序。"""
    markdown = "开头\n![第一张](https://img.example/1.png)\n![第二张](https://img.example/2.jpg)"

    assert extract_markdown_image_urls(markdown) == [
        "https://img.example/1.png",
        "https://img.example/2.jpg",
    ]


def test_analyze_reference_images_excludes_qrcode_and_keeps_original_binding():
    """过滤二维码后，剩余描述仍须绑定它自己的 URL 与原始位置。"""
    urls = ["https://img.example/first.png", "https://img.example/qr.png", "https://img.example/last.png"]

    result = analyze_reference_images(
        urls,
        lambda _: [
            {"subject": "第一张", "is_qrcode": False},
            {"subject": "二维码", "is_qrcode": True},
            {"subject": "最后一张", "is_qrcode": False},
        ],
    )

    assert [image.source_url for image in result.usable_images] == [urls[0], urls[2]]
    assert [image.source_index for image in result.usable_images] == [0, 2]
    assert [image.description["subject"] for image in result.usable_images] == ["第一张", "最后一张"]
    assert result.skipped_qrcode_count == 1
    assert result.skipped_qrcode_source_indexes == (1,)


def test_analyze_markdown_reference_images_returns_no_usable_images_when_all_are_qrcodes():
    """全是二维码时，任何后续提示词构建与图片生成都必须停止。"""
    result = analyze_markdown_reference_images(
        "![二维码](https://img.example/qr.png)",
        lambda _: [{"subject": "二维码", "is_qrcode": True}],
    )

    assert result.usable_images == ()
    assert result.skipped_qrcode_count == 1
