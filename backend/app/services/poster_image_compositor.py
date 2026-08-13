"""纯海报图片的程序化文字合成服务。

图片模型适合生成真实产品、室内空间和光影氛围，但中文长文案、品牌电话等像素级
内容不应该依赖模型自由绘制。本模块只负责把已经由文案 Agent 生成的短文案稳定
叠加到海报上方安全区；产品主体与空间仍来自图生图结果，朦胧质感由程序作稳定
收敛，避免不同图片供应商造成视觉风格漂移。

该服务不参与普通 HTML 仿写，也不改变绣蔓旧海报模板。只有新三品牌通用模板将
图片节点标记为 ``programmatic_text_v1`` 后，最终归档阶段才会调用这里。
"""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
from PIL import ImageEnhance, ImageFilter


PROGRAMMATIC_POSTER_TEXT_OVERLAY_MODE = "programmatic_text_v1"

# Windows 开发环境与 Linux 容器分别提供中文字体。字体查找放在合成层，避免图片
# 生成供应商和文章后处理模块互相依赖；找不到中文字体时仍然使用 Pillow 回退字体，
# 不能因为字体包缺失而丢弃整篇文章。
_FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    r"/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    r"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def apply_poster_text_overlay(
    image_bytes: bytes,
    *,
    copy: str,
    kind: str = "content",
    content_type: str = "image/jpeg",
) -> bytes:
    """将海报文案绘制到上方留白区并返回同格式图片字节。

    ``kind=title`` 使用较大的标题字号，其他类型使用正文海报字号。文字会按
    最终画布宽度自动换行和缩小，避免长文案越界；绘制只增加轻微阴影，不铺整条
    背景色带，保留模型生成的朦胧光影和产品空间关系。
    """

    normalized_copy = _normalize_copy(copy)
    if not image_bytes or not normalized_copy:
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as source:
        source.load()
        image = source.convert("RGBA")

    width, height = image.size
    if width < 64 or height < 64:
        return image_bytes

    is_title = str(kind or "").strip().lower() == "title"
    font_size = _fit_font_size(width, is_title=is_title)
    max_width = max(32, int(width * 0.78))
    font = _load_font(font_size)
    lines, font = _fit_copy_lines(normalized_copy, font, max_width, font_size)
    if not lines:
        return image_bytes

    probe = ImageDraw.Draw(image)
    line_boxes = [probe.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [max(font_size, box[3] - box[1]) for box in line_boxes]
    # 参考图使用“短句逐行”的编辑感，行距明显大于普通正文；海报不是把文字
    # 塞进一个紧凑文本框，而是让每一行都成为画面中的独立呼吸单元。
    line_gap = max(22, round(font_size * 1.05))
    paragraph_gap = max(30, round(font_size * 1.25))
    paragraph_breaks = _find_paragraph_breaks(lines)
    total_height = sum(line_heights) + line_gap * (len(lines) - 1)
    total_height += paragraph_gap * len(paragraph_breaks)
    top_ratio = 0.20 if is_title else 0.16
    top = max(24, int(height * top_ratio))
    # 文字安全区是上半部的有限区域。若极端长文案仍然超出，就向上压缩，不能
    # 覆盖产品主体；文案 Agent 的正常长度会在这里保持充足留白。
    if top + total_height > int(height * 0.48):
        top = max(24, int(height * 0.48) - total_height)

    # 参考海报的朦胧感不是“空白背景”，而是产品和空间仍然可辨认、整体对比度
    # 被柔光轻轻压低的视觉层。这里使用“低透明度暖色滤镜 + 少量模糊图层”的组合，
    # 由程序稳定执行，避免每次换图片模型后风格漂移；透明度足够低，不会遮住 ERP
    # 产品主体，也不会把餐桌、柜体等真实场景变成一整块雾色。
    image = _apply_hazy_visual_treatment(image)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    current_y = top
    for line_index, (line, line_height, box) in enumerate(zip(lines, line_heights, line_boxes)):
        if line_index in paragraph_breaks:
            current_y += paragraph_gap
        line_width = box[2] - box[0]
        x = max(0, (width - line_width) // 2)
        # 轻微浅色边缘让深色文字在窗光或深色材质上仍然清楚，但不使用底板，
        # 这样程序叠字与参考海报的留白感一致。
        draw.text(
            (x + 1, current_y + 1),
            line,
            font=font,
            fill=(255, 255, 255, 150),
            stroke_width=1,
            stroke_fill=(255, 255, 255, 90),
        )
        draw.text(
            (x, current_y),
            line,
            font=font,
            fill=(62, 48, 39, 238),
            stroke_width=1,
            stroke_fill=(255, 247, 235, 110),
        )
        current_y += line_height + line_gap

    result = Image.alpha_composite(image, overlay)
    return _encode_image(result, content_type)


def build_continuous_poster_slices(
    image_bytes: bytes,
    *,
    panel_image_bytes: tuple[bytes, ...] | list[bytes] | None = None,
    copies: tuple[str, ...] | list[str],
    kinds: tuple[str, ...] | list[str] | None = None,
    content_type: str = "image/jpeg",
    slice_size: tuple[int, int] = (1024, 1536),
) -> list[bytes]:
    """把一张主视觉渲染为连续长海报，并按固定高度切成多张图片。

    连续性必须在切片之前建立：背景、产品叠层、暖灰渐变和雾化统一作用于同一
    张母版，切线处不会重新开始场景或滤镜。主视觉模型只生成一张普通竖图，
    程序将其作为清晰主体放在母版中部，同时使用柔焦延展层填满整张长画布；这样
    既保留真实产品，也不要求图片模型支持超长尺寸，且将图片模型调用量降为一次。
    """

    if not image_bytes or not copies:
        return []
    slice_width, slice_height = slice_size
    if slice_width < 64 or slice_height < 64:
        raise ValueError("连续海报切片尺寸过小")
    with Image.open(io.BytesIO(image_bytes)) as source:
        source.load()
        source_rgba = source.convert("RGBA")

    count = len(copies)
    canvas_height = slice_height * count
    if count == 3:
        # 三图海报是当前业务中的主模板。它不应把一张竖图暴力拉成三倍高度，
        # 而是以“标题空间 -> 完整产品 -> 空间余韵”组织同一场景；三段边缘再
        # 做渐隐混合，视觉上保持连贯，构图上也有明确的阅读节奏。
        master = _build_three_panel_poster_master(
            source_rgba,
            slice_width=slice_width,
            slice_height=slice_height,
            panel_sources=_load_panel_sources(panel_image_bytes, expected_count=count),
        )
        return _render_poster_master_slices(
            master,
            copies=copies,
            kinds=kinds,
            content_type=content_type,
            slice_size=slice_size,
        )

    # 背景用柔焦后的纵向延展层承载连续色彩和光影，细节刻意柔化以避免拉伸痕迹。
    background_source = ImageOps.fit(
        source_rgba,
        (slice_width, slice_height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.46),
    )
    background = background_source.resize(
        (slice_width, canvas_height), Image.Resampling.BICUBIC
    ).filter(ImageFilter.GaussianBlur(radius=max(5, round(slice_width * 0.006))))
    background = ImageEnhance.Color(background).enhance(0.78)
    background = ImageEnhance.Contrast(background).enhance(0.90)

    # 在柔焦底层上保留一层很低透明度的原始空间细节。它不承担主体清晰度，
    # 只防止第三段退化成没有方向感的纯色雾面；透明度沿母版向下渐增，保证标题
    # 页干净、后半段仍能读到墙面、家具和光影的连续关系。
    detail = background_source.resize(
        (slice_width, canvas_height), Image.Resampling.BICUBIC
    )
    detail = ImageEnhance.Color(detail).enhance(0.72)
    detail = ImageEnhance.Contrast(detail).enhance(0.92)
    detail_mask = Image.new("L", (slice_width, canvas_height), 0)
    detail_mask_draw = ImageDraw.Draw(detail_mask)
    detail_start = round(canvas_height * 0.22)
    for y in range(canvas_height):
        alpha = round(34 * max(0.0, min(1.0, (y - detail_start) / max(1, canvas_height * 0.45))))
        detail_mask_draw.line((0, y, slice_width, y), fill=alpha)
    detail.putalpha(detail_mask)

    # 纵向暖灰渐变是连续母版的一部分，不在每个切片上重复绘制，避免边界色块。
    gradient = Image.new("RGBA", (slice_width, canvas_height), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(canvas_height):
        progress = y / max(1, canvas_height - 1)
        center_weight = 1.0 - abs(progress * 2.0 - 1.0)
        alpha = round(28 + 32 * center_weight)
        gradient_draw.line((0, y, slice_width, y), fill=(196, 183, 166, alpha))
    master = Image.alpha_composite(background, gradient)
    master = Image.alpha_composite(master, detail)

    # 清晰主体只保留一份，放在中部并在上下边缘渐隐，防止三段各自出现一个产品。
    foreground = ImageOps.fit(
        source_rgba,
        (slice_width, slice_height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.54),
    )
    foreground = ImageEnhance.Color(foreground).enhance(0.86)
    mask = Image.new("L", (slice_width, slice_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    fade = max(90, slice_height // 5)
    for y in range(slice_height):
        edge_fade = min(1.0, y / fade, (slice_height - 1 - y) / fade)
        mask_draw.line((0, y, slice_width, y), fill=round(208 * max(0.0, edge_fade)))
    foreground.putalpha(ImageChops.multiply(foreground.getchannel("A"), mask))
    # 主体从第一段后半部自然进入，跨过第一、二段的连接处；第三段只保留空间
    # 余韵，避免产品被复制三次，也避免首段只剩一块没有内容的灰色背景。
    foreground_y = max(0, round(slice_height * 0.42))
    master.alpha_composite(foreground, (0, foreground_y))

    return _render_poster_master_slices(
        master,
        copies=copies,
        kinds=kinds,
        content_type=content_type,
        slice_size=slice_size,
    )


def _build_three_panel_poster_master(
    source: Image.Image,
    *,
    slice_width: int,
    slice_height: int,
    panel_sources: tuple[Image.Image, ...] | None = None,
) -> Image.Image:
    """以三幕结构组织同一张产品图，避免纵向强拉伸造成产品重复。

    第一幕只取原图上部的空间和光线，给标题充分留白；第二幕完整保留产品，
    是整组海报唯一清晰主体；第三幕从产品底部和地面取材并柔和淡出。这样仍是
    同一房间、同一色温和同一光线，却不会出现一张沙发在三页里反复出现的错误。
    """

    source_panels = panel_sources if panel_sources and len(panel_sources) == 3 else (source, source, source)
    title_source, hero_source, outro_source = source_panels
    hero = ImageOps.fit(
        hero_source,
        (slice_width, slice_height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.52),
    )
    hero = ImageEnhance.Color(hero).enhance(0.88)
    hero = ImageEnhance.Contrast(hero).enhance(0.94)

    # 传入三张来源图时，首图保留独立的空间广角，避免再从主视觉顶部裁出一张
    # 空白背景；兼容旧调用时仍沿用单图上部取景，确保历史测试任务不受影响。
    if panel_sources and len(panel_sources) == 3:
        title_panel = ImageOps.fit(
            title_source,
            (slice_width, slice_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.46),
        )
    else:
        title_crop = hero.crop((0, 0, slice_width, max(64, round(slice_height * 0.32))))
        title_panel = title_crop.resize((slice_width, slice_height), Image.Resampling.BICUBIC)
    title_panel = title_panel.filter(ImageFilter.GaussianBlur(radius=max(5, round(slice_width * 0.006))))
    title_panel = ImageEnhance.Color(title_panel).enhance(0.76)
    title_panel = ImageEnhance.Contrast(title_panel).enhance(0.86)
    title_panel = Image.alpha_composite(
        title_panel,
        Image.new("RGBA", title_panel.size, (204, 191, 174, 92)),
    )

    # 收尾页从原图底部的地面/产品落点取材，只保留低对比空间细节；它从完整
    # 产品页自然退场，给最后一段文案和联系方式留下干净位置。
    if panel_sources and len(panel_sources) == 3:
        outro_panel = ImageOps.fit(
            outro_source,
            (slice_width, slice_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.52, 0.58),
        )
    else:
        outro_crop = hero.crop((0, round(slice_height * 0.74), slice_width, slice_height))
        outro_panel = outro_crop.resize((slice_width, slice_height), Image.Resampling.BICUBIC)
    outro_panel = outro_panel.filter(ImageFilter.GaussianBlur(radius=max(4, round(slice_width * 0.004))))
    outro_panel = ImageEnhance.Color(outro_panel).enhance(0.78)
    outro_panel = ImageEnhance.Contrast(outro_panel).enhance(0.88)
    outro_panel = Image.alpha_composite(
        outro_panel,
        Image.new("RGBA", outro_panel.size, (198, 184, 166, 70)),
    )

    master = Image.new("RGBA", (slice_width, slice_height * 3), (0, 0, 0, 0))
    master.alpha_composite(title_panel, (0, 0))
    master.alpha_composite(hero, (0, slice_height))
    master.alpha_composite(outro_panel, (0, slice_height * 2))
    _blend_panel_seam(master, title_panel, hero, center_y=slice_height)
    _blend_panel_seam(master, hero, outro_panel, center_y=slice_height * 2)
    return master


def _load_panel_sources(panel_image_bytes: tuple[bytes, ...] | list[bytes] | None, *, expected_count: int) -> tuple[Image.Image, ...] | None:
    """解码三张来源图；不完整输入回退旧单主视觉逻辑，避免中断历史文章发布。"""

    if not panel_image_bytes or len(panel_image_bytes) != expected_count:
        return None
    panels: list[Image.Image] = []
    for raw_image in panel_image_bytes:
        if not raw_image:
            return None
        with Image.open(io.BytesIO(raw_image)) as panel:
            panel.load()
            panels.append(panel.convert("RGBA"))
    return tuple(panels)


def _blend_panel_seam(
    master: Image.Image,
    upper: Image.Image,
    lower: Image.Image,
    *,
    center_y: int,
) -> None:
    """在两段之间绘制连续渐变，隐藏程序分区而不添加可见分割线。"""

    width, height = upper.size
    blend_height = max(96, round(height * 0.11))
    upper_tail = upper.crop((0, height - blend_height, width, height)).resize(
        (width, blend_height * 2), Image.Resampling.BICUBIC
    )
    lower_head = lower.crop((0, 0, width, blend_height)).resize(
        (width, blend_height * 2), Image.Resampling.BICUBIC
    )
    mask = Image.new("L", (width, blend_height * 2), 0)
    mask_draw = ImageDraw.Draw(mask)
    for y in range(blend_height * 2):
        mask_draw.line((0, y, width, y), fill=round(255 * y / max(1, blend_height * 2 - 1)))
    # 使用同一渐变蒙版控制下层透明度，避免两张图在交接带简单叠加而变暗。
    transition = Image.composite(lower_head, upper_tail, mask)
    master.alpha_composite(transition, (0, center_y - blend_height))


def _render_poster_master_slices(
    master: Image.Image,
    *,
    copies: tuple[str, ...] | list[str],
    kinds: tuple[str, ...] | list[str] | None,
    content_type: str,
    slice_size: tuple[int, int],
) -> list[bytes]:
    """在连续母版上排版各段文案，再按目标尺寸切出可上传的图片。"""

    slice_width, slice_height = slice_size
    normalized_kinds = list(kinds or ())
    is_three_panel_story = len(copies) == 3
    for index, copy in enumerate(copies):
        kind = normalized_kinds[index] if index < len(normalized_kinds) else "content"
        # 完整产品页的文字放在更靠上的空间留白，避免压住产品中心；标题和收尾
        # 仍保留稍大的呼吸距离，和参考图的编辑式排版一致。
        top_ratio = 0.10 if is_three_panel_story and index == 1 else None
        master = _draw_poster_copy(
            master,
            copy=copy,
            kind=kind,
            y_offset=index * slice_height,
            layout_height=slice_height,
            top_ratio=top_ratio,
        )
    return [
        _encode_image(
            master.crop((0, index * slice_height, slice_width, (index + 1) * slice_height)),
            content_type,
        )
        for index in range(len(copies))
    ]


def _normalize_copy(value: object) -> str:
    """清除换行和引号噪声，保证图片中只绘制文案本身。"""

    return re.sub(r"\s+", "", str(value or "")).strip("“”\"'")[:160]


def _fit_font_size(width: int, *, is_title: bool) -> int:
    """按固定画布宽度计算标题/正文海报字号，避免随供应商尺寸漂移。"""

    ratio = 0.058 if is_title else 0.032
    minimum = 30 if is_title else 22
    maximum = 68 if is_title else 40
    return max(minimum, min(maximum, round(width * ratio)))


def _fit_copy_lines(
    copy: str,
    font: ImageFont.ImageFont,
    max_width: int,
    initial_size: int,
) -> tuple[list[str], ImageFont.ImageFont]:
    """在不超过安全宽度的前提下换行，必要时逐级缩小字体。"""

    current_font = font
    for size in range(initial_size, 15, -2):
        if size != initial_size:
            current_font = _load_font(size)
        lines = _wrap_copy(copy, current_font, max_width)
        # 参考图式文案允许最多七行，足够承载“两组铺垫 + 一组设问”这类叙事；
        # 过长时才缩小字号，不能为了三行限制破坏原有语义节奏。
        if lines and len(lines) <= 7:
            return lines, current_font
    return _wrap_copy(copy, current_font, max_width), current_font


def _wrap_copy(copy: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """按语义单元折行，兼容中文、英文和电话号码混排。

    逗号、分号和句号是海报文案的自然呼吸点。先保留这些标点形成的短句，只有
    单个短句本身超过安全宽度时才退回逐字符换行，避免原实现把一句话切成阅读
    体验很差的“每行几个字”。较长的并列句还会在连接词处平衡分行，使每一行
    都接近参考图的居中短句比例。
    """

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    semantic_units = [unit for unit in re.split(r"(?<=[，。；！？：])", copy) if unit]
    if not semantic_units:
        semantic_units = [copy]

    lines: list[str] = []
    for unit in semantic_units:
        # 海报的理想单行宽度低于物理安全宽度。超过理想宽度的语义单元优先
        # 在连接词处拆成两行，形成参考图中长短均衡、视觉居中的文字列。
        preferred_lines = _split_for_reference_line_length(
            unit,
            probe,
            font,
            max_width,
        )
        if preferred_lines:
            lines.extend(preferred_lines)
            continue
        if _text_width(probe, unit, font) <= max_width:
            lines.append(unit)
            continue

        # 极长的单个语义单元（例如没有标点的产品说明）才逐字拆分，且不丢失原文。
        current = ""
        for character in unit:
            candidate = current + character
            if current and _text_width(probe, candidate, font) > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)

    return lines


def _split_for_reference_line_length(
    unit: str,
    probe: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str] | None:
    """在长语义句的连接词处断行，避免画布够宽却视觉过长。

    参考图不是按像素宽度把文字塞满，而是把一行控制在约七成安全宽度内；当句子
    因引号、并列关系或转折而偏长时，在“和、与、及、或、而”等连接词处优先分开。
    只在两边都能保持可读长度时生效，其余情况交给普通宽度判断，避免误切产品名。
    """

    preferred_width = max(80, round(max_width * 0.72))
    if _text_width(probe, unit, font) <= preferred_width:
        return None

    candidates: list[tuple[int, int]] = []
    for match in re.finditer(r"[和与及或而]", unit):
        split_at = match.start() + 1
        left = unit[:split_at]
        right = unit[split_at:]
        if not left or not right:
            continue
        left_width = _text_width(probe, left, font)
        right_width = _text_width(probe, right, font)
        if left_width <= preferred_width and right_width <= preferred_width:
            # 选择两边宽度最接近的切点，避免一边只有两三个字。
            candidates.append((abs(left_width - right_width), split_at))
    if not candidates:
        return None

    _, split_at = min(candidates)
    return [unit[:split_at], unit[split_at:]]


def _find_paragraph_breaks(lines: list[str]) -> set[int]:
    """识别参考图式的段落转折点，在设问或结论前增加一组留白。

    规则优先识别常见的中文转折/设问开头；没有明显连接词时，长文案在中点附近
    选择一个以分号、句号或问号结束的行作为备用断点。短文案不强行制造空白，
    保证同一排版器可以覆盖不同品牌和不同内容长度。
    """

    if len(lines) < 5:
        return set()
    break_starters = re.compile(r"^(难道|其实|因此|所以|于是|原来|然而|但是|可惜|那么)")
    for index, line in enumerate(lines[1:], start=1):
        if break_starters.match(line):
            return {index}

    midpoint = len(lines) // 2
    for offset in range(len(lines)):
        candidates = (midpoint - offset, midpoint + offset)
        for index in candidates:
            if 0 < index < len(lines) and re.search(r"[；。！？]$", lines[index - 1]):
                return {index}
    return set()


def _text_width(probe: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    """统一测量文字宽度，集中处理 Pillow 不同版本的边界返回值。"""

    box = probe.textbbox((0, 0), text, font=font)
    return max(0, box[2] - box[0])


def _apply_hazy_visual_treatment(image: Image.Image) -> Image.Image:
    """给显式海报图片增加通用朦胧质感，并保持产品与空间仍可辨认。

    该处理位于最终图片归档边界，任何图片模型、ERP 原图尺寸或品牌文案都可以
    复用；它不改变普通 HTML 文章，因为普通文章不会携带 poster_copy。暖色层
    模拟参考图的柔和空气感，低强度模糊层降低硬边对比度，轻微降低颜色饱和度则
    避免不同品牌素材在同一组海报中出现突兀的高饱和跳变。
    """

    base = image.convert("RGBA")
    softened = base.filter(ImageFilter.GaussianBlur(radius=max(3, round(base.width * 0.006))))
    # 中等强度混合让背景真正产生柔焦空气感，同时保留约七成原图边缘信息；
    # 这比单纯增加色块透明度更接近参考图，也不会把产品轮廓直接抹掉。
    hazy = Image.blend(base, softened, 0.22)
    muted = ImageEnhance.Color(hazy).enhance(0.78)
    muted = ImageEnhance.Contrast(muted).enhance(0.86)
    warm_layer = Image.new("RGBA", muted.size, (196, 183, 166, 84))
    return Image.alpha_composite(muted, warm_layer)


def _draw_poster_copy(
    image: Image.Image,
    *,
    copy: str,
    kind: str,
    y_offset: int = 0,
    layout_height: int | None = None,
    top_ratio: float | None = None,
) -> Image.Image:
    """在指定的母版分区绘制一组参考图式文案，不重新处理整张背景。

    ``y_offset`` 让三段文案共享一个母版坐标系；单张海报旧入口保持 offset=0，
    因此旧模板的像素输出边界和新连续模板彼此隔离。
    """

    normalized_copy = _normalize_copy(copy)
    if not normalized_copy:
        return image
    width, height = image.size
    region_height = layout_height or height
    is_title = str(kind or "").strip().lower() == "title"
    font_size = _fit_font_size(width, is_title=is_title)
    max_width = max(32, int(width * 0.78))
    font = _load_font(font_size)
    lines, font = _fit_copy_lines(normalized_copy, font, max_width, font_size)
    if not lines:
        return image
    probe = ImageDraw.Draw(image)
    line_boxes = [probe.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [max(font_size, box[3] - box[1]) for box in line_boxes]
    line_gap = max(22, round(font_size * 1.05))
    paragraph_gap = max(30, round(font_size * 1.25))
    paragraph_breaks = _find_paragraph_breaks(lines)
    total_height = sum(line_heights) + line_gap * (len(lines) - 1)
    total_height += paragraph_gap * len(paragraph_breaks)
    resolved_top_ratio = (
        max(0.04, min(0.36, float(top_ratio)))
        if top_ratio is not None
        else (0.20 if is_title else 0.16)
    )
    top = max(24, int(region_height * resolved_top_ratio))
    if top + total_height > int(region_height * 0.48):
        top = max(24, int(region_height * 0.48) - total_height)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    current_y = y_offset + top
    for line_index, (line, line_height, box) in enumerate(zip(lines, line_heights, line_boxes)):
        if line_index in paragraph_breaks:
            current_y += paragraph_gap
        line_width = box[2] - box[0]
        x = max(0, (width - line_width) // 2)
        # 参考图式文案以实色为主，不使用白色描边或发光边；只保留极淡的偏移
        # 作为复杂背景上的可读性保险，避免文字看起来像贴纸或模型水印。
        draw.text((x + 1, current_y + 1), line, font=font, fill=(255, 252, 242, 74), stroke_width=0)
        draw.text((x, current_y), line, font=font, fill=(62, 48, 39, 232), stroke_width=0)
        current_y += line_height + line_gap
    return Image.alpha_composite(image, overlay)


def _encode_image(image: Image.Image, content_type: str) -> bytes:
    """以归档层要求的 MIME 格式编码图片，集中处理 JPEG 的质量参数。"""

    output = io.BytesIO()
    output_format = _content_type_to_format(content_type)
    save_kwargs: dict[str, Any] = {"format": output_format}
    if output_format == "JPEG":
        save_kwargs.update({"quality": 94, "optimize": True})
    image.convert("RGB").save(output, **save_kwargs)
    return output.getvalue()


def _load_font(size: int) -> ImageFont.ImageFont:
    """按环境加载中文字体，回退字体也尽量保持请求字号。"""

    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _content_type_to_format(content_type: str) -> str:
    """将 MIME 类型映射为归档图片编码。"""

    normalized = str(content_type or "").lower()
    if "png" in normalized:
        return "PNG"
    if "webp" in normalized:
        return "WEBP"
    return "JPEG"
