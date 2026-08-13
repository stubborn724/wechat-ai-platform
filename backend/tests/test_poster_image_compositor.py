"""纯海报程序叠字服务的像素级回归测试。"""

from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def reset_test_tables():
    """程序叠字是纯内存逻辑，不应触发项目级 MySQL 清理。"""

    yield


def test_apply_poster_text_overlay_writes_copy_into_upper_safe_area():
    """标题/正文文案必须真实落到图片上方安全区，而不是只存在 HTML 元数据里。"""
    from app.services.poster_image_compositor import apply_poster_text_overlay

    original = Image.new("RGB", (1024, 1536), "#ded6ca")
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    rendered = apply_poster_text_overlay(
        buffer.getvalue(),
        copy="东意西形",
        kind="title",
        content_type="image/png",
    )

    result = Image.open(BytesIO(rendered)).convert("RGB")
    assert result.size == (1024, 1536)
    assert any(
        result.getpixel((x, y)) != original.getpixel((x, y))
        for x in range(180, 844)
        for y in range(100, 500)
    )
    assert result.getpixel((60, 400)) != original.getpixel((60, 400))


def test_apply_poster_text_overlay_adds_generic_hazy_visual_treatment():
    """通用海报必须增加低对比度雾化层，同时保留产品图像的基本轮廓。"""
    from app.services.poster_image_compositor import apply_poster_text_overlay

    original = Image.new("RGB", (1024, 1536), "#ffffff")
    pixels = original.load()
    for x in range(0, 1024, 32):
        for y in range(0, 1536, 32):
            pixels[x, y] = (20, 30, 40)
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    rendered = apply_poster_text_overlay(
        buffer.getvalue(),
        copy="安静地，让生活展开",
        kind="content",
        content_type="image/png",
    )

    result = Image.open(BytesIO(rendered)).convert("RGB")
    # 统一雾化层必须改变远离文字安全区的背景像素，而不只是叠上方文字。
    assert result.getpixel((500, 900)) != original.getpixel((500, 900))
    assert len(result.getcolors(maxcolors=1024) or []) > 1


def test_hazy_visual_treatment_is_visible_enough_to_match_reference_mood():
    """朦胧层必须明显降低纯白背景的刺眼程度，而不是只有肉眼不可见的微调。"""
    from app.services.poster_image_compositor import apply_poster_text_overlay

    original = Image.new("RGB", (1024, 1536), "#ffffff")
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    rendered = apply_poster_text_overlay(
        buffer.getvalue(),
        copy="安静地生活",
        kind="content",
        content_type="image/png",
    )

    result = Image.open(BytesIO(rendered)).convert("RGB")
    red, green, blue = result.getpixel((500, 900))
    assert red <= 240
    assert green <= 238
    assert blue <= 235


def test_poster_copy_is_wrapped_by_semantic_punctuation_before_character_fallback():
    """海报文案应优先按标点断句，保证完整语义行而不是逐字断行。"""
    from app.services.poster_image_compositor import _wrap_copy, _load_font

    lines = _wrap_copy(
        "矩形之境，如一页轻柔剪纸，透出几何光影；餐桌成为日常的安静仪式",
        _load_font(32),
        740,
    )

    assert lines == [
        "矩形之境，",
        "如一页轻柔剪纸，",
        "透出几何光影；",
        "餐桌成为日常的安静仪式",
    ]


def test_poster_copy_uses_reference_style_for_multi_line_story_copy():
    """参考图式海报必须保留每个短句，不能把叙事文案压缩成三行。"""
    from app.services.poster_image_compositor import _wrap_copy, _load_font

    lines = _wrap_copy(
        "想给毛孩子足够的活动空间，又怕家里堆得乱七八糟；"
        "想让猫自由穿梭，又怕它抓坏刚买的新家具；"
        "难道养宠的家，就只能在“人的舒适”和“猫的快乐”之间二选一？",
        _load_font(32),
        740,
    )

    assert lines == [
        "想给毛孩子足够的活动空间，",
        "又怕家里堆得乱七八糟；",
        "想让猫自由穿梭，",
        "又怕它抓坏刚买的新家具；",
        "难道养宠的家，",
        "就只能在“人的舒适”和",
        "“猫的快乐”之间二选一？",
    ]


def test_build_continuous_poster_slices_uses_one_master_canvas_without_cut_lines():
    """连续海报应先渲染母版再切片，切线处不能重新开始背景或渐变。"""
    from app.services.poster_image_compositor import build_continuous_poster_slices

    original = Image.new("RGB", (1024, 1536))
    pixels = original.load()
    for y in range(1536):
        color = (40 + y // 18, 70 + y // 25, 100 + y // 30)
        for x in range(1024):
            pixels[x, y] = color
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    slices = build_continuous_poster_slices(
        buffer.getvalue(),
        copies=("东意西形", "让空间回到从容。", "木与光，在日常里安静相遇。"),
        kinds=("title", "content", "content"),
        content_type="image/png",
    )

    rendered = [Image.open(BytesIO(item)).convert("RGB") for item in slices]
    assert [item.size for item in rendered] == [(1024, 1536)] * 3
    # 同一列跨切线的颜色变化必须连续且很小；独立生成/独立套滤镜会在此产生明显跳变。
    for upper, lower in zip(rendered, rendered[1:]):
        upper_pixel = upper.getpixel((64, 1535))
        lower_pixel = lower.getpixel((64, 0))
        assert max(abs(a - b) for a, b in zip(upper_pixel, lower_pixel)) < 8
    # 中段仍须保留可读的主视觉纹理，不能把连续海报处理成一整张纯色雾面。
    assert len(rendered[1].getcolors(maxcolors=1_000_000) or []) > 64


def test_continuous_poster_uses_a_clean_title_intro_and_a_clear_middle_product_hero():
    """三图海报应有标题引入和主体页，不能把产品生硬拉进首图和末图。"""
    from app.services.poster_image_compositor import build_continuous_poster_slices

    original = Image.new("RGB", (1024, 1536), "#d9d1c5")
    pixels = original.load()
    for x in range(250, 774):
        for y in range(760, 1240):
            pixels[x, y] = (28, 35, 39) if (x // 16 + y // 16) % 2 else (235, 225, 210)
    buffer = BytesIO()
    original.save(buffer, format="PNG")

    slices = build_continuous_poster_slices(
        buffer.getvalue(),
        copies=("光影里的东方礼赞", "让空间回到从容。", "木与光，在日常里安静相遇。"),
        content_type="image/png",
    )
    first = Image.open(BytesIO(slices[0])).convert("RGB")
    second = Image.open(BytesIO(slices[1])).convert("RGB")

    # 首图是同场景标题页，不能出现被拉长的产品主体；第二图才是完整、清晰的产品页。
    intro_colors = first.crop((250, 980, 774, 1450)).getcolors(maxcolors=1_000_000) or []
    intro_luminance = [sum(color[:3]) / 3 for _count, color in intro_colors]
    hero_colors = second.crop((250, 520, 774, 1040)).getcolors(maxcolors=1_000_000) or []
    hero_luminance = [sum(color[:3]) / 3 for _count, color in hero_colors]
    assert max(intro_luminance) - min(intro_luminance) < 65
    assert max(hero_luminance) - min(hero_luminance) > 85


def test_continuous_poster_keeps_three_distinct_product_views_with_faded_transitions():
    """三视角海报应保留三张来源图的主体色彩，连接处只做渐隐而不复制主图。"""
    from app.services.poster_image_compositor import build_continuous_poster_slices

    source_images = []
    for color in ((176, 86, 72), (84, 138, 103), (83, 111, 164)):
        image = Image.new("RGB", (1024, 1536), color)
        # 为每个来源图提供可见纹理，避免测试只验证纯色底板。
        for y in range(500, 1150, 48):
            Image.Image.paste(image, tuple(min(255, channel + 28) for channel in color), (180, y, 844, y + 18))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        source_images.append(buffer.getvalue())

    slices = build_continuous_poster_slices(
        source_images[0],
        panel_image_bytes=tuple(source_images),
        copies=("光影里的餐桌", "围坐时，岩板的冷静与木色的温度彼此平衡。", "边缘、支撑与留白，让每一次用餐都回到舒服的节奏。"),
        kinds=("title", "content", "content"),
        content_type="image/png",
    )

    rendered = [Image.open(BytesIO(item)).convert("RGB") for item in slices]
    centers = [image.getpixel((120, 1250)) for image in rendered]
    assert centers[0][0] > centers[0][1]
    assert centers[1][1] > centers[1][0]
    assert centers[2][2] > centers[2][0]
