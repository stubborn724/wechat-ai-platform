"""文章发布前的确定性内容收口服务。

大模型只负责撰写正文和生成画面，不能承担来源联系方式清理、品牌水印和合规说明
这些必须稳定生效的职责。本模块在图片归档与正文最终落库之间提供可复用的后处理：

1. 为每张文章图片绘制统一品牌联系方式水印；
2. 将处理后的图片归档地址回写到文章状态；
3. 在最终正文末尾追加且只追加一次 AI 图片说明。

服务刻意不感知投喂源、ERP 或特定模型，以便所有文章入口复用同一发布规范。
"""

from __future__ import annotations

import io
import logging
import re
import asyncio
from html import escape
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.services.poster_image_compositor import build_continuous_poster_slices
from app.services.url_safety import validate_url


if TYPE_CHECKING:
    from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

AI_IMAGE_DISCLAIMER = "部分图片AI生成，具体以实际产品为准。"
_DISCLAIMER_ATTRIBUTE = "data-ai-image-disclaimer"
_CJK_FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    r"/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


@dataclass(frozen=True)
class ArticleImageAttribution:
    """单张文章图片的统一品牌水印内容与位置快照。

    水印不再拼接 ERP 产品型号，避免长编号破坏画面，并与用户给出的右下角示例
    保持一致。位置、边距和透明度在任务快照中保存，保证不同公众号可以使用不同
    水印，而普通历史文章仍使用默认右下角样式。
    """

    lines: tuple[str, ...]
    position: str = "bottom-right"
    margin: int | None = None
    opacity: float = 0.9


class ArticleImageNormalizationError(RuntimeError):
    """最终正文存在未能归档署名的图片时阻止发布。

    图片地址一旦绕过归档，就会同时失去动态署名、长期可用地址和微信中转站的
    统一交付保障。因此这里必须显式失败，不能沿用旧逻辑静默保留上游短时 URL。
    """


@dataclass(frozen=True)
class NormalizedArticleImages:
    """最终文章图片归档结果。

    ``url_mapping`` 用于同步 ArticleState 中的图片地址，``body_image_urls`` 则
    按正文 DOM 顺序提供给封面选择逻辑，确保封面与正文使用同一份带署名素材。
    """

    content: str
    url_mapping: dict[str, str]
    body_image_urls: tuple[str, ...]


async def download_image_bytes(image_url: str) -> tuple[bytes, str]:
    """下载连续海报主视觉一次并返回字节与 MIME 类型。

    连续海报需要在同一个母版上完成处理，不能让三个切片分别重新下载上游地址；
    单独抽出下载边界也让测试可以验证“只下载一次”，并复用已有 URL 安全校验。
    """

    validate_url(image_url)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(image_url)
        response.raise_for_status()
    return response.content, response.headers.get("Content-Type", "image/jpeg")


def build_article_image_attribution(
    product_name: str | None,
    brand_contact: str | None = None,
) -> ArticleImageAttribution:
    """构建右下角单行品牌电话水印。

    ``product_name`` 保留在函数签名中是为了不破坏归档调用方的领域接口，但图片上
    不再绘制产品编号或名称。长型号会挤压家居画面，且用户明确指定了示例中的
    “品牌名 + TEL”单行形式；产品名称仍会保留在文章标题、正文和素材文件名中。
    """

    del product_name
    raw_contact = str(brand_contact or settings.article_image_brand_contact)
    normalized_contact = " ".join(raw_contact.split())[:128]
    # 兼容历史配置“绣蔓家具TEL:186...”与人工输入的中英文冒号写法，统一成
    # 示例的“绣蔓家具 TEL:186...”形态，避免同一公众号出现多个水印版本。
    normalized_contact = re.sub(
        r"\s*(?:tel)\s*[:：]?\s*",
        " TEL:",
        normalized_contact,
        flags=re.IGNORECASE,
    )
    return ArticleImageAttribution(
        lines=(normalized_contact,) if normalized_contact else (),
    )


def apply_article_image_attribution_to_bytes(
    image_bytes: bytes,
    *,
    attribution: ArticleImageAttribution,
    content_type: str = "image/jpeg",
    font_size: int | None = None,
) -> bytes:
    """在图片右下角绘制示例风格的单行品牌水印并保留原始尺寸。

    水印不使用整条深色底栏，避免遮挡家具画面。字号按原图宽度计算，保证图片在
    微信正文中缩小展示后仍接近参考图的阅读比例；深灰文字配极轻的浅色偏移，
    只提升复杂背景上的边缘辨识度，不把水印变成带底色的横条。
    """

    if not image_bytes or not attribution.lines:
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGBA")

    width, height = image.size
    if width < 32 or height < 32:
        logger.warning("图片尺寸过小，跳过文章署名: %sx%s", width, height)
        return image_bytes

    # 公众号正文会把原图缩小展示，字号需要保留可读性，但不能按旧的 6% 比例
    # 放大成遮挡主体的横幅。统一交给独立函数计算，后续调整水印策略只改一个
    # 参数，同时让单元测试可以直接验证不同原图宽度的边界。
    configured_margin = attribution.margin
    margin = (
        max(0, int(configured_margin))
        if configured_margin is not None
        else max(18, int(min(width, height) * 0.04))
    )
    # 普通文章沿用按原图宽度动态计算；定时 ERP 图片在归一化为统一画布后，
    # 由调用方显式传入 24px，避免不同供应商的原始尺寸再次改变水印大小。
    resolved_font_size = (
        calculate_article_attribution_font_size(width)
        if font_size is None
        else max(1, int(font_size))
    )
    font_size = resolved_font_size
    letter_spacing = _article_attribution_letter_spacing(font_size)
    font = _load_font(font_size)
    text_lines = _fit_attribution_lines(
        attribution.lines,
        font=font,
        max_width=width - margin * 2,
        letter_spacing=letter_spacing,
    )
    if not text_lines:
        return image_bytes

    draw_probe = ImageDraw.Draw(image)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    # 当前业务水印固定为一行；仍以循环实现，保障旧归档数据意外传入多行时各行
    # 也从右下角向上排列，而不是回退为覆盖整幅图片的底栏。
    line_gap = max(3, font_size // 5)
    line_boxes = [draw_probe.textbbox((0, 0), line, font=font) for line in text_lines]
    total_height = sum(max(box[3] - box[1], font_size) for box in line_boxes)
    total_height += line_gap * (len(line_boxes) - 1)
    # Pillow 的 ``textbbox`` 顶部常包含字体基线偏移；减去首行偏移后，视觉文字
    # 的顶部/底部才会严格落在目标位置，而不是看起来比计算值更贴近边缘。
    first_top_offset = line_boxes[0][1]
    position = str(attribution.position or "bottom-right").strip().lower()
    if position in {"top-left", "top-right"}:
        current_y = max(margin, margin - first_top_offset)
    elif position == "center":
        current_y = max(margin, (height - total_height - first_top_offset) // 2)
    else:
        current_y = max(margin, height - margin - total_height - first_top_offset)
    opacity = max(0.0, min(1.0, float(attribution.opacity)))
    for line, box in zip(text_lines, line_boxes):
        line_width = _measure_attribution_line(
            draw_probe,
            line,
            font=font,
            letter_spacing=letter_spacing,
        )
        line_height = max(box[3] - box[1], font_size)
        if position in {"top-left", "bottom-left"}:
            current_x = margin
        elif position == "center":
            current_x = max(margin, (width - line_width) // 2)
        else:
            current_x = max(margin, width - margin - line_width)
        # 只绘制一像素级的浅色偏移，不绘制矩形背景或底部色带，保持参考图的轻量
        # 文字形式；主色提高不透明度，避免浅色家具背景把联系方式冲淡。
        _draw_attribution_line(
            overlay_draw,
            line,
            (current_x + 1, current_y + 1),
            font=font,
            fill=(255, 255, 255, round(110 * opacity)),
            letter_spacing=letter_spacing,
        )
        _draw_attribution_line(
            overlay_draw,
            line,
            (current_x, current_y),
            font=font,
            fill=(74, 68, 62, round(230 * opacity)),
            letter_spacing=letter_spacing,
        )
        current_y += line_height + line_gap

    result = Image.alpha_composite(image, overlay)
    output = io.BytesIO()
    format_name = _resolve_image_format(content_type)
    save_kwargs: dict[str, Any] = {"format": format_name}
    if format_name == "JPEG":
        save_kwargs["quality"] = 94
        save_kwargs["optimize"] = True
    result.convert("RGB").save(output, **save_kwargs)
    return output.getvalue()


def calculate_article_attribution_font_size(width: int) -> int:
    """按原图宽度计算动态水印字号。

    1024px 图片使用 36px 字号，明显小于历史 61px 左右的水印；18px 和 36px
    的上下限分别保护窄图可读性与大图不被水印压住。输入异常时按最小字号处理，
    这样图片处理服务不会因为配置或模型返回了异常尺寸而抛出除零错误。
    """

    normalized_width = max(1, int(width or 1))
    return max(18, min(36, round(normalized_width * 0.035)))


def _article_attribution_letter_spacing(font_size: int) -> int:
    """计算水印字符间距，保持单行联系方式的横向可读性。

    缩小字号后，中文与英文电话混排会明显收窄。少量字符间距只扩展横向排布，
    不会增加文字高度或形成底色横条；上限为 4px，避免水印重新变得松散、醒目。
    """

    return max(0, min(4, round(max(1, int(font_size)) * 0.1)))


def _measure_attribution_line(
    probe: ImageDraw.ImageDraw,
    line: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    letter_spacing: int,
) -> int:
    """测量带字符间距的单行水印宽度，供截断与右对齐共用。"""

    if not line:
        return 0
    natural_width = probe.textbbox((0, 0), line, font=font)[2]
    return natural_width + max(0, len(line) - 1) * max(0, letter_spacing)


def _draw_attribution_line(
    draw: ImageDraw.ImageDraw,
    line: str,
    origin: tuple[int, int],
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    letter_spacing: int,
) -> None:
    """按字符绘制单行水印，以支持中文字体和英文电话的统一字距。"""

    x, y = origin
    spacing = max(0, int(letter_spacing))
    for character in line:
        draw.text((x, y), character, font=font, fill=fill)
        character_width = draw.textbbox((0, 0), character, font=font)[2]
        x += character_width + spacing


async def archive_state_images_with_attribution(
    db: "Session",
    state: Any,
    *,
    tenant_id: int,
) -> dict[str, str]:
    """归档状态中的文章图片并将 URL 改为可长期引用的归档地址。

    文章正文尚未最终渲染时必须先做该步骤，之后 ``merge_images_into_content`` 才能
    使用带署名的新 URL 填回 HTML 槽位。单张归档失败保留模型原地址并记录日志，
    不会使已经成功的其它图片丢失。
    """

    images = list(getattr(state, "images", []) or [])
    if not images:
        return {}

    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.storage_service import storage_service

    product_name = getattr(state, "product_name", None)
    if not product_name:
        title = getattr(state, "title", None)
        product_name = getattr(title, "main_title", None) or getattr(state, "topic", "")
    attribution = build_article_image_attribution(product_name)
    url_mapping: dict[str, str] = {}

    for image in images:
        source_url = str(getattr(image, "url", "") or "").strip()
        if not source_url:
            continue
        try:
            asset = await save_image_to_asset_library(
                db,
                tenant_id,
                source_url,
                keywords=str(getattr(image, "keywords", "") or ""),
                usage_type="article_image",
                article_image_attribution=attribution,
                original_filename=product_name or None,
            )
        except Exception as exc:
            logger.warning("文章图片归档与署名失败，保留原始地址: %s", exc)
            continue
        if asset and asset.storage_key:
            attributed_url = storage_service.get_url(asset.storage_key)
            image.url = attributed_url
            url_mapping[source_url] = attributed_url
    return url_mapping


async def archive_image_urls_with_attribution(
    db: "Session",
    image_urls: list[str],
    *,
    tenant_id: int,
    product_name: str | None,
) -> list[str]:
    """为不经过 ``ArticleState`` 的纯图片任务归档并叠加相同署名。

    纯图片任务没有正文图片槽位和 ``ArticleState.images``，但发布规范不能因此
    分裂。本函数复用同一归档参数和署名值对象，仅负责按原始顺序返回最终图片地址。
    """

    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.storage_service import storage_service

    attribution = build_article_image_attribution(product_name)
    finalized_urls: list[str] = []
    for source_url in image_urls:
        normalized_source_url = str(source_url or "").strip()
        if not normalized_source_url:
            continue
        try:
            asset = await save_image_to_asset_library(
                db,
                tenant_id,
                normalized_source_url,
                keywords=(product_name or "")[:50],
                usage_type="article_image",
                article_image_attribution=attribution,
                original_filename=product_name or None,
            )
        except Exception as exc:
            logger.warning("纯图片任务归档与署名失败，保留原始地址: %s", exc)
            finalized_urls.append(normalized_source_url)
            continue
        finalized_urls.append(
            storage_service.get_url(asset.storage_key)
            if asset and asset.storage_key
            else normalized_source_url
        )
    return finalized_urls


async def normalize_final_article_images_with_attribution(
    db: "Session",
    *,
    content: str,
    tenant_id: int,
    product_name: str | None,
    target_size: tuple[int, int] | None = None,
    watermark_font_size: int | None = None,
    watermark_enabled: bool | None = None,
    task_watermark_config: dict[str, Any] | None = None,
) -> NormalizedArticleImages:
    """统一归档最终正文中的每一张业务图片并回填 HTML。

    早期流程只遍历 ``ArticleState.images``，而 HTML 仿写模板、封面回填或业务
    重试可能带来没有出现在该列表里的 ``img`` 节点，造成同一篇文章水印不一致。
    这里以最终 HTML 为唯一真相：除固定页脚/二维码外，所有图片均必须成功归档，
    否则抛出异常阻止草稿或直接发布。``watermark_enabled`` 是定时任务的显式
    开关：传入 ``False`` 时同时关闭租户 Logo/文字水印和动态品牌署名；传入
    ``True`` 时启用两者；未传入时保留历史调用的全局配置回退与动态署名行为。
    ``task_watermark_config`` 非空时优先级最高，表示任务已经锁定水印快照；它
    会覆盖租户全局样式，文字类型复用本服务的中文字体渲染器，Logo 类型交给
    素材归档层的 Logo 渲染器处理。
    """

    normalized_content = str(content or "")
    if not normalized_content.strip():
        return NormalizedArticleImages(normalized_content, {}, ())

    soup = BeautifulSoup(normalized_content, "html.parser")
    from app.services.scheduled_task_watermark_service import (
        normalize_task_watermark_config,
    )

    normalized_task_watermark_config = normalize_task_watermark_config(
        task_watermark_config
    )
    if normalized_task_watermark_config is not None:
        # 任务快照是唯一水印来源。文字快照使用当前已验证过的无底色单行绘制
        # 逻辑；Logo 或关闭状态不能再叠加动态品牌署名。
        if (
            normalized_task_watermark_config["enabled"]
            and normalized_task_watermark_config["type"] == "text"
        ):
            attribution = ArticleImageAttribution(
                lines=(normalized_task_watermark_config["content"],),
                position=normalized_task_watermark_config["position"],
                margin=normalized_task_watermark_config["margin"],
                opacity=normalized_task_watermark_config["opacity"],
            )
        else:
            attribution = None
        effective_watermark_enabled = bool(
            normalized_task_watermark_config["enabled"]
        )
        effective_watermark_font_size = (
            watermark_font_size
            or normalized_task_watermark_config["font_size"]
        )
    else:
        # 定时任务需要一个真正的“全关”状态，不能只关闭租户配置而让动态品牌署名
        # 继续出现。None 只服务于旧的普通文章调用，保持其既有兼容行为。
        attribution = (
            None
            if watermark_enabled is False
            else build_article_image_attribution(product_name)
        )
        effective_watermark_enabled = watermark_enabled
        effective_watermark_font_size = watermark_font_size
    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.storage_service import storage_service

    url_mapping: dict[str, str] = {}
    body_image_urls: list[str] = []
    continuous_archived_urls: dict[int, str] = {}
    archive_requests: dict[str, dict[str, Any]] = {}

    # 程序叠字海报在这里批处理：同一容器内的多个 img 只是同一张母版的逻辑
    # 切片，必须先下载主视觉一次，再按文案顺序切出独立归档对象。普通文章、旧
    # 海报模板和没有完整 poster_copy 标记的内容继续走下面原有的逐图流程。
    for container in soup.find_all(attrs={"data-ai-layout": "seamless-poster"}):
        nodes = [node for node in container.find_all("img") if str(node.get("src", "") or "").strip()]
        source_urls = [str(node.get("src", "") or "").strip() for node in nodes]
        copies = [str(node.get("data-poster-copy", "") or "").strip() for node in nodes]
        if len(nodes) < 2 or not all(copies):
            continue
        downloaded_panels = [await download_image_bytes(source_url) for source_url in source_urls]
        master_bytes, master_content_type = downloaded_panels[0]
        slices = build_continuous_poster_slices(
            master_bytes,
            panel_image_bytes=(
                tuple(item[0] for item in downloaded_panels)
                if len(set(source_urls)) > 1
                else None
            ),
            copies=tuple(copies),
            kinds=tuple(str(node.get("data-poster-kind", "content") or "content") for node in nodes),
            content_type=master_content_type,
            slice_size=target_size or (1024, 1536),
        )
        if len(slices) != len(nodes):
            raise ArticleImageNormalizationError("连续海报切片数量与正文图片数量不一致")
        for slice_index, (node, slice_bytes, copy) in enumerate(zip(nodes, slices, copies)):
            poster_kind = str(node.get("data-poster-kind", "content") or "content").strip()
            is_last_slice = slice_index == len(slices) - 1
            asset = await save_image_to_asset_library(
                db,
                tenant_id,
                source_urls[slice_index],
                keywords=(product_name or "")[:50],
                usage_type="article_image",
                # 连续海报视为一件完整作品，动态文字水印只放在最终一段；否则
                # 第二张底部的联系方式会紧贴第三张顶部，视觉上像重复的脏边。
                article_image_attribution=attribution if is_last_slice else None,
                original_filename=product_name or None,
                # 连续合成已经输出最终切片尺寸，不能再次被普通 ERP 画布规则缩放。
                target_size=None,
                watermark_font_size=effective_watermark_font_size,
                watermark_enabled=(effective_watermark_enabled if is_last_slice else False),
                task_watermark_config=(normalized_task_watermark_config if is_last_slice else None),
                # 母版合成器已经完成文案与全局雾化；这里禁止单图归档器再次
                # 处理 poster_copy，否则每个切片会重新套滤镜，切线处出现色差。
                poster_copy=None,
                poster_kind=poster_kind,
                image_bytes=slice_bytes,
                image_content_type=master_content_type,
            )
            if not asset or not asset.storage_key:
                raise ArticleImageNormalizationError(
                    f"连续海报切片无法归档，已停止发布：{source_urls[slice_index][:160]}"
                )
            archived_url = storage_service.get_url(asset.storage_key)
            continuous_archived_urls[id(node)] = archived_url

    body_images = []
    for image in soup.find_all("img"):
        source_url = str(image.get("src", "") or "").strip()
        if not source_url:
            continue
        if _is_fixed_footer_image(image):
            continue
        continuous_url = continuous_archived_urls.get(id(image))
        if continuous_url:
            image["src"] = continuous_url
            body_image_urls.append(continuous_url)
            continue

        # 同一图片可能同时作为封面、首图或被响应式容器重复引用，只归档一次，
        # 避免重复下载、重复计费和双重叠加署名。
        body_images.append((image, source_url))
        if source_url not in archive_requests:
            archive_requests[source_url] = {
                "poster_copy": str(image.get("data-poster-copy", "") or "").strip() or None,
                "poster_kind": str(image.get("data-poster-kind", "content") or "content").strip(),
            }

    if archive_requests:
        archive_started_at = asyncio.get_running_loop().time()
        logger.info(
            "开始并发归档正文图片 tenant_id=%s count=%d",
            tenant_id,
            len(archive_requests),
        )

        async def _download_body_image(source_url: str) -> tuple[str, bytes, str]:
            """并发下载单张正文图片，返回源地址、字节和 MIME 类型。

            图片下载是发布前最容易被外部网络拖慢的阶段，且各图片之间没有依赖。
            数据库写入不在这里执行，避免同一个 SQLAlchemy Session 被多个协程
            交错使用；后续归档仍按稳定顺序逐张入库。
            """

            image_bytes, content_type = await download_image_bytes(source_url)
            return source_url, image_bytes, content_type

        downloaded_items = await asyncio.gather(*[
            _download_body_image(source_url)
            for source_url in archive_requests
        ])
        downloaded_images = {
            source_url: (image_bytes, content_type)
            for source_url, image_bytes, content_type in downloaded_items
        }

        archived_pairs = []
        for source_url, options in archive_requests.items():
            image_bytes, image_content_type = downloaded_images[source_url]
            asset = await save_image_to_asset_library(
                db,
                tenant_id,
                source_url,
                keywords=(product_name or "")[:50],
                usage_type="article_image",
                article_image_attribution=attribution,
                original_filename=product_name or None,
                target_size=target_size,
                watermark_font_size=effective_watermark_font_size,
                watermark_enabled=effective_watermark_enabled,
                task_watermark_config=normalized_task_watermark_config,
                poster_copy=options["poster_copy"],
                poster_kind=options["poster_kind"],
                image_bytes=image_bytes,
                image_content_type=image_content_type,
            )
            if not asset or not asset.storage_key:
                raise ArticleImageNormalizationError(
                    f"正文图片无法归档署名，已停止发布：{source_url[:160]}"
                )
            archived_pairs.append((source_url, storage_service.get_url(asset.storage_key)))
        url_mapping.update(dict(archived_pairs))
        logger.info(
            "正文图片并发归档完成 tenant_id=%s count=%d elapsed=%.2fs",
            tenant_id,
            len(archived_pairs),
            asyncio.get_running_loop().time() - archive_started_at,
        )

    for image, source_url in body_images:
        attributed_url = url_mapping[source_url]
        image["src"] = attributed_url
        body_image_urls.append(attributed_url)

    return NormalizedArticleImages(
        content=str(soup),
        url_mapping=url_mapping,
        body_image_urls=tuple(body_image_urls),
    )


def _is_fixed_footer_image(image: Any) -> bool:
    """识别固定页脚中的二维码，避免给联系方式二维码叠加产品名称。

    页脚统一由 ``data-ai-footer-template`` 标记；历史模板缺失标记时再依据图片
    的 alt/src 联系标识兜底。普通正文图片即使含有文字也不会被该规则误排除。
    """

    parent = image.parent
    while parent is not None:
        if getattr(parent, "attrs", {}).get("data-ai-footer-template"):
            return True
        parent = getattr(parent, "parent", None)

    identity = " ".join([
        str(image.get("alt", "") or ""),
        str(image.get("src", "") or ""),
    ]).lower()
    return any(keyword in identity for keyword in (
        "二维码", "qrcode", "qr-code", "企业微信", "wechat", "weixin",
    ))


def replace_article_image_urls(content: str, url_mapping: dict[str, str]) -> str:
    """把归档后的图片地址精确回填最终正文，兼容 HTML 转义后的查询参数。"""

    normalized_content = str(content or "")
    for source_url, attributed_url in url_mapping.items():
        normalized_content = normalized_content.replace(source_url, attributed_url)
        normalized_content = normalized_content.replace(
            escape(source_url, quote=True),
            escape(attributed_url, quote=True),
        )
    return normalized_content


def append_ai_image_disclaimer(content: str) -> str:
    """在最终正文的最末尾追加一次 AI 图片说明。

    使用数据属性作为幂等标记，避免自动保存、再次发布或多个处理入口让说明重复。
    HTML 正文渲染为独立段落；旧 Markdown 正文则保持其原有文本格式。
    """

    normalized_content = str(content or "")
    if not normalized_content.strip():
        return normalized_content
    if _DISCLAIMER_ATTRIBUTE in normalized_content or AI_IMAGE_DISCLAIMER in normalized_content:
        return normalized_content

    if normalized_content.lstrip().startswith("<"):
        soup = BeautifulSoup(normalized_content, "html.parser")
        disclaimer = soup.new_tag("p")
        disclaimer[_DISCLAIMER_ATTRIBUTE] = "appended"
        disclaimer["style"] = "font-size:12px;line-height:1.7;color:#888;margin:24px 0 0;text-align:center;"
        disclaimer.string = AI_IMAGE_DISCLAIMER
        soup.append(disclaimer)
        return str(soup)

    return f"{normalized_content.rstrip()}\n\n{AI_IMAGE_DISCLAIMER}"


def _load_font(font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """加载可按请求字号缩放的中文字体，避免容器缺字时水印静默变小。

    Worker 镜像通过 ``fonts-noto-cjk`` 提供 Linux 字体，Windows 开发环境则优先
    使用微软雅黑。最后的 Pillow 回退只负责保留字号，属于环境异常下的可见降级；
    正式发布仍应依赖前面的 CJK 字体，否则中文字符可能无法完整呈现。
    """

    for path in _CJK_FONT_PATHS:
        try:
            return ImageFont.truetype(path, font_size)
        except (OSError, IOError):
            continue
    logger.warning("未找到中文字体，文章图片署名将使用 Pillow 可缩放回退字体")
    try:
        return ImageFont.load_default(size=max(1, int(font_size)))
    except TypeError:
        # 兼容旧版 Pillow 没有 ``size`` 参数的环境；镜像中的 CJK 字体正常时不会
        # 走到这里，但保留旧版本兼容分支可以避免开发环境直接启动失败。
        return ImageFont.load_default()


def _fit_attribution_lines(
    lines: tuple[str, ...],
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    letter_spacing: int = 0,
) -> tuple[str, ...]:
    """按字符截断过长署名，防止产品编号把底部文字挤出图片。"""

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    fitted: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        while len(line) > 1 and _measure_attribution_line(
            probe,
            line,
            font=font,
            letter_spacing=letter_spacing,
        ) > max_width:
            candidate = line[:-2].rstrip()
            line = f"{candidate}…" if candidate else "…"
        # 当图片极窄时连一个字符也无法容纳。丢弃该行比无限截断或绘制越界更安全。
        if line and _measure_attribution_line(
            probe,
            line,
            font=font,
            letter_spacing=letter_spacing,
        ) <= max_width:
            fitted.append(line)
    return tuple(fitted)


def _resolve_image_format(content_type: str) -> str:
    """根据 MIME 类型选择 Pillow 编码格式，未知格式统一转 JPEG 保证可发布。"""

    normalized_type = str(content_type or "").lower()
    if "png" in normalized_type:
        return "PNG"
    if "webp" in normalized_type:
        return "WEBP"
    return "JPEG"
