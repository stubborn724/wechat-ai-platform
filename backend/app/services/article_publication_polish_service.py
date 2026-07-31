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
from html import escape
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app.config import settings


if TYPE_CHECKING:
    from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

AI_IMAGE_DISCLAIMER = "部分图片AI生成，具体以实际产品为准。"
_DISCLAIMER_ATTRIBUTE = "data-ai-image-disclaimer"
_CJK_FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


@dataclass(frozen=True)
class ArticleImageAttribution:
    """单张文章图片右下角的统一品牌水印内容。

    水印不再拼接 ERP 产品型号，避免长编号破坏画面，并与用户给出的右下角示例
    保持一致。品牌联系方式集中从服务端配置读取，归档与正文回填共用同一值对象。
    """

    lines: tuple[str, ...]


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
) -> bytes:
    """在图片右下角绘制示例风格的单行品牌水印并保留原始尺寸。

    水印不使用整条深色底栏，避免遮挡家具画面。使用右下角的小字号深灰文字和
    轻微浅色阴影，在浅色地毯和深色木材场景都能辨认；文字由程序绘制，不会出现
    模型错别字、缺字或随机位置。
    """

    if not image_bytes or not attribution.lines:
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGBA")

    width, height = image.size
    if width < 32 or height < 32:
        logger.warning("图片尺寸过小，跳过文章署名: %sx%s", width, height)
        return image_bytes

    # 示例水印与图片边缘保留约一行呼吸空间；过小会让文字看起来贴边，过大又会
    # 压缩产品画面，因此按短边比例计算并设定 22px 的最小边距。
    margin = max(22, int(min(width, height) * 0.05))
    font_size = max(14, min(30, int(width * 0.027)))
    font = _load_font(font_size)
    text_lines = _fit_attribution_lines(
        attribution.lines,
        font=font,
        max_width=width - margin * 2,
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
    # 的底部才会严格落在 ``margin`` 以内，而不是看起来比计算值更贴近底边。
    first_top_offset = line_boxes[0][1]
    current_y = max(margin, height - margin - total_height - first_top_offset)
    for line, box in zip(text_lines, line_boxes):
        line_width = box[2] - box[0]
        line_height = max(box[3] - box[1], font_size)
        current_x = max(margin, width - margin - line_width)
        # 使用低透明浅色阴影勾勒深灰正文，保持示例中“无底板、低调可读”的效果。
        overlay_draw.text(
            (current_x + 1, current_y + 1),
            line,
            font=font,
            fill=(255, 255, 255, 96),
        )
        overlay_draw.text(
            (current_x, current_y),
            line,
            font=font,
            fill=(70, 62, 56, 205),
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
) -> NormalizedArticleImages:
    """统一归档最终正文中的每一张业务图片并回填 HTML。

    早期流程只遍历 ``ArticleState.images``，而 HTML 仿写模板、封面回填或业务
    重试可能带来没有出现在该列表里的 ``img`` 节点，造成同一篇文章水印不一致。
    这里以最终 HTML 为唯一真相：除固定页脚/二维码外，所有图片均必须成功归档并
    叠加动态署名，否则抛出异常阻止草稿或直接发布。
    """

    normalized_content = str(content or "")
    if not normalized_content.strip():
        return NormalizedArticleImages(normalized_content, {}, ())

    soup = BeautifulSoup(normalized_content, "html.parser")
    attribution = build_article_image_attribution(product_name)
    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.storage_service import storage_service

    url_mapping: dict[str, str] = {}
    body_image_urls: list[str] = []
    for image in soup.find_all("img"):
        source_url = str(image.get("src", "") or "").strip()
        if not source_url:
            continue
        if _is_fixed_footer_image(image):
            continue

        # 同一图片可能同时作为封面、首图或被响应式容器重复引用，只归档一次，
        # 避免重复下载、重复计费和双重叠加署名。
        attributed_url = url_mapping.get(source_url)
        if attributed_url is None:
            asset = await save_image_to_asset_library(
                db,
                tenant_id,
                source_url,
                keywords=(product_name or "")[:50],
                usage_type="article_image",
                article_image_attribution=attribution,
                original_filename=product_name or None,
            )
            if not asset or not asset.storage_key:
                raise ArticleImageNormalizationError(
                    f"正文图片无法归档署名，已停止发布：{source_url[:160]}"
                )
            attributed_url = storage_service.get_url(asset.storage_key)
            url_mapping[source_url] = attributed_url

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
    """优先使用支持中文的系统字体，部署环境缺失时退回 Pillow 默认字体。"""

    for path in _CJK_FONT_PATHS:
        try:
            return ImageFont.truetype(path, font_size)
        except (OSError, IOError):
            continue
    logger.warning("未找到中文字体，文章图片署名将使用 Pillow 默认字体")
    return ImageFont.load_default()


def _fit_attribution_lines(
    lines: tuple[str, ...],
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> tuple[str, ...]:
    """按字符截断过长署名，防止产品编号把底部文字挤出图片。"""

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    fitted: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        while len(line) > 1 and probe.textbbox((0, 0), line, font=font)[2] > max_width:
            candidate = line[:-2].rstrip()
            line = f"{candidate}…" if candidate else "…"
        # 当图片极窄时连一个字符也无法容纳。丢弃该行比无限截断或绘制越界更安全。
        if line and probe.textbbox((0, 0), line, font=font)[2] <= max_width:
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
