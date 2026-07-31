"""HTML 仿写的结构保护服务。

本模块不调用大模型，只负责三件必须由程序保证的事情：
1. 从投喂文章 HTML 中提取可生成的文字槽位与图片槽位；
2. 保留原有 DOM 的节点顺序、容器、class 与行内样式；
3. 将 Agent 返回的文字和图片精确写回各自槽位。

把 DOM 操作从 Agent 调用层抽离，是为了避免模型直接输出整段 HTML 时误删
标签、移动图片，或把格式说明混入正文。模型只生成内容，程序拥有结构所有权。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Mapping

from bs4 import BeautifulSoup, NavigableString, Tag

from app.schemas.article import ImageRequirement
from app.services.reference_contact_filter_service import (
    is_reference_contact_image_identity,
    is_reference_contact_text,
)


_TEXT_TAG_NAMES = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "li", "figcaption")
_UNSAFE_TAG_NAMES = ("script", "iframe", "object", "embed", "form", "input", "button")


@dataclass(frozen=True)
class HtmlTextSlot:
    """一个可由文字生成 Agent 填充的 HTML 文本节点说明。"""

    slot_id: str
    tag_name: str
    original_text: str
    target_length: int


@dataclass(frozen=True)
class HtmlImageSlot:
    """一个需要由图片分析和图片生成 Agent 处理的原位图片节点说明。"""

    slot_id: str
    source_url: str
    original_alt: str
    position: int


@dataclass(frozen=True)
class HtmlImitationBlueprint:
    """投喂文章的不可变 HTML 模板及其可填充槽位。

    ``html_template`` 只含内部槽位标记，绝不用于直接展示或发布。发布前必须先
    通过 ``render_html_imitation`` 写入文字，再通过 ``replace_html_image_slots``
    写入已生成的公网图片地址。
    """

    html_template: str
    text_slots: tuple[HtmlTextSlot, ...]
    image_slots: tuple[HtmlImageSlot, ...]

    def prompt_payload(
        self,
        excluded_image_slot_ids: AbstractSet[str] = frozenset(),
        *,
        include_source_urls: bool = True,
    ) -> dict:
        """返回适合内容 Agent 的最小语义描述，避免把原文正文交给模型复述。

        ERP 图生图仅需要投喂源的图片槽位顺序，视觉主体始终来自 ERP 原图；此时
        关闭 ``include_source_urls`` 可避免把带签名的参考 URL 重复传入文本模型。
        传统参考图仿写仍保留 URL，确保视觉理解路径的行为不变。
        """
        image_slots = []
        for slot in self.image_slots:
            if slot.slot_id in excluded_image_slot_ids:
                continue
            payload = {
                "id": slot.slot_id,
                "position": slot.position,
                "reference_alt": slot.original_alt,
            }
            if include_source_urls:
                payload["source_url"] = slot.source_url
            image_slots.append(payload)
        return {
            "text_slots": [
                {
                    "id": slot.slot_id,
                    "tag": slot.tag_name,
                    "target_length": slot.target_length,
                }
                for slot in self.text_slots
            ],
            "image_slots": image_slots,
        }


@dataclass(frozen=True)
class HtmlImitationRenderResult:
    """文字已回填、图片仍待生成的中间 HTML 结果。"""

    html: str
    image_requirements: tuple[ImageRequirement, ...]


def select_html_image_slots(
    blueprint: HtmlImitationBlueprint,
    *,
    excluded_image_slot_ids: AbstractSet[str] = frozenset(),
    max_generated_images: int = 5,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """选择最多五个普通图片槽位，并返回需要留空的其余槽位。

    选择严格遵循投喂源 DOM 顺序，从而保持图片与前后文字的对应关系。二维码等排除
    槽位不计入成本上限，也不会进入留空集合；当投喂源只有四张普通图时自然生成
    四张，不为凑数量额外创建不存在的节点。
    """

    if max_generated_images < 1:
        raise ValueError("max_generated_images 必须至少为 1")
    eligible_slot_ids = tuple(
        slot.slot_id
        for slot in blueprint.image_slots
        if slot.slot_id not in excluded_image_slot_ids
    )
    return (
        eligible_slot_ids[:max_generated_images],
        eligible_slot_ids[max_generated_images:],
    )


def analyze_html_for_imitation(html: str) -> HtmlImitationBlueprint:
    """将投喂文章 HTML 转成可仿写蓝图。

    只选取最外层的文本块，避免 ``p > strong`` 这类嵌套节点被重复生成。图片不会
    被删除或移动，仅被打上稳定槽位 ID，后续生成图会替换该节点的 ``src`` 属性。
    """
    if not html or not html.strip():
        raise ValueError("HTML 仿写需要非空的投喂文章 HTML")

    soup = BeautifulSoup(html, "html.parser")
    for unsafe_tag in soup.find_all(_UNSAFE_TAG_NAMES):
        unsafe_tag.decompose()
    _remove_reference_contact_sections(soup)

    text_slots: list[HtmlTextSlot] = []
    image_slots: list[HtmlImageSlot] = []

    for tag in soup.find_all(_TEXT_TAG_NAMES):
        if _has_text_block_ancestor(tag):
            continue
        if tag.find("img"):
            # 图文混排容器中的图片不能被清空；只让其内部独立文本块参与后续分析。
            continue

        original_text = tag.get_text(" ", strip=True)
        if not original_text:
            continue

        slot_id = f"text-{len(text_slots) + 1}"
        tag["data-ai-text-slot"] = slot_id
        tag.clear()
        tag.append(NavigableString(_text_marker(slot_id)))
        text_slots.append(
            HtmlTextSlot(
                slot_id=slot_id,
                tag_name=tag.name.lower(),
                original_text=original_text,
                target_length=len(original_text),
            )
        )

    for image in soup.find_all("img"):
        slot_id = f"image-{len(image_slots) + 1}"
        source_url = str(image.get("src", image.get("data-src", ""))).strip()
        original_alt = str(image.get("alt", "")).strip()
        image["data-ai-image-slot"] = slot_id
        image["src"] = _image_marker(slot_id)
        image_slots.append(
            HtmlImageSlot(
                slot_id=slot_id,
                source_url=source_url,
                original_alt=original_alt,
                position=len(image_slots) + 1,
            )
        )

    if not text_slots:
        raise ValueError("投喂文章中未找到可生成的文字 HTML 块")

    return HtmlImitationBlueprint(
        html_template=str(soup),
        text_slots=tuple(text_slots),
        image_slots=tuple(image_slots),
    )


def render_html_imitation(
    blueprint: HtmlImitationBlueprint,
    *,
    text_by_slot: Mapping[str, str],
    image_by_slot: Mapping[str, Mapping[str, str]],
    excluded_image_slot_ids: AbstractSet[str] = frozenset(),
    empty_image_slot_ids: AbstractSet[str] = frozenset(),
    footer_template: str = "",
) -> HtmlImitationRenderResult:
    """将内容 Agent 的 JSON 结果回填为 HTML 与图片需求。

    文字按普通文本节点写入，BeautifulSoup 会自动转义模型返回的尖括号，防止模型
    通过正文破坏模板 DOM。未返回文字的槽位保持为空，绝不回退复制参考原文。
    """
    soup = BeautifulSoup(blueprint.html_template, "html.parser")

    for tag in soup.find_all(attrs={"data-ai-text-slot": True}):
        slot_id = str(tag["data-ai-text-slot"])
        content = _normalise_generated_text(text_by_slot.get(slot_id, ""))
        if tag.name and tag.name.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            content = _normalise_heading_text(content)
        tag.clear()
        tag.append(NavigableString(content))
        del tag["data-ai-text-slot"]

    image_requirements: list[ImageRequirement] = []
    for image_slot in blueprint.image_slots:
        image = soup.find("img", attrs={"data-ai-image-slot": image_slot.slot_id})
        if image is None and image_slot.slot_id in excluded_image_slot_ids:
            # 多个二维码可能位于同一卡片；第一个槽位替换整个区域后，后续图片节点
            # 已不存在，此时视为同一区域已经处理完成。
            continue
        if image is None:
            raise ValueError(f"HTML 模板缺少图片槽位: {image_slot.slot_id}")

        if image_slot.slot_id in excluded_image_slot_ids:
            # 视觉分析后才识别出的二维码同样必须删除。固定页脚统一在循环结束后
            # 追加，绝不复用参考文章的联系卡容器、颜色或布局。
            _remove_reference_contact_region(image)
            continue

        if image_slot.slot_id in empty_image_slot_ids:
            # 超出成本上限的普通图片只移除 img，自身 figure/div 容器继续保留为空，
            # 这样文章文字和整体 DOM 顺序不发生变化。
            image.decompose()
            continue

        generated = image_by_slot.get(image_slot.slot_id, {})
        keywords = _normalise_generated_text(generated.get("keywords", ""))
        prompt = _normalise_generated_text(generated.get("prompt", ""))
        if keywords:
            image["alt"] = keywords
        image["src"] = _image_marker(image_slot.slot_id)
        image_requirements.append(
            ImageRequirement(
                position=image_slot.position,
                type="inline",
                section_title="",
                image_source="DASHSCOPE",
                keywords=keywords,
                prompt=prompt,
                placeholder_id=image_slot.slot_id,
            )
        )

    _append_configured_footer(soup, footer_template)

    return HtmlImitationRenderResult(
        html=str(soup),
        image_requirements=tuple(image_requirements),
    )


def replace_html_image_slots(html: str, image_urls_by_slot: Mapping[str, str]) -> str:
    """把图片生成结果替换回原来的 ``img`` 节点。

    该函数不新增节点、不改变节点顺序。缺少图片地址时保留内部标记，让上层能够
    显式识别不完整结果，而不是悄悄把图片追加到文末造成版式错乱。
    """
    soup = BeautifulSoup(html, "html.parser")
    for image in soup.find_all("img", attrs={"data-ai-image-slot": True}):
        slot_id = str(image["data-ai-image-slot"])
        url = (image_urls_by_slot.get(slot_id) or "").strip()
        if not url:
            continue
        image["src"] = url
        del image["data-ai-image-slot"]
    return str(soup)


def _has_text_block_ancestor(tag: Tag) -> bool:
    """判断节点是否已经被某个外层可生成文本块覆盖。"""
    return any(
        isinstance(parent, Tag) and parent.name in _TEXT_TAG_NAMES
        for parent in tag.parents
    )


def _normalise_generated_text(value: str) -> str:
    """清理模型输出中的空白，但保留正常中文段落和标点。"""
    return " ".join((value or "").split())


def _normalise_heading_text(value: str) -> str:
    """去除标题末尾无语义的连接性标点，保留问号和感叹号等表达性标点。"""
    return value.rstrip("，、；：")


def _remove_reference_contact_sections(soup: BeautifulSoup) -> None:
    """在创建槽位前剔除投喂源联系区，阻断参考联系方式进入任何 Agent。

    解析阶段处理是根因修复：若等 Agent 生成后再替换二维码，原电话、购买提示已经
    作为文本槽位进入模型上下文。优先删除独立的 ``aside/footer/div`` 联系卡；当参考
    HTML 没有包裹容器时仅删除命中的最小文本或图片节点，避免误删整篇文章。
    """

    candidate_regions: list[Tag] = []
    for tag in soup.find_all(True):
        if _is_reference_contact_marker(tag):
            candidate_regions.append(_find_reference_contact_region(tag))

    # 多个标记通常属于同一联系卡。先保留最外层节点，再删除，避免父节点删除后继续
    # 操作已失效的子节点。
    unique_regions: list[Tag] = []
    for region in candidate_regions:
        if any(region is existing or region in existing.descendants for existing in unique_regions):
            continue
        unique_regions = [
            existing
            for existing in unique_regions
            if existing not in region.descendants
        ]
        unique_regions.append(region)

    for region in unique_regions:
        region.decompose()


def _is_reference_contact_marker(tag: Tag) -> bool:
    """判断节点是否为参考文章的联系方式、二维码或购买引导。"""

    if tag.name == "img":
        image_identity = " ".join(
            str(tag.get(attribute, ""))
            for attribute in ("src", "data-src", "alt", "title", "class", "id")
        ).lower()
        return is_reference_contact_image_identity(image_identity)

    if tag.name in {"div", "aside", "footer", "section"}:
        # 容器的 get_text 会汇总整篇正文。这里只看自身标识，避免正文 section 因其
        # 内部含有一行联系方式而被误删；真正文字命中由下方最小文本块处理。
        identity = " ".join(
            str(tag.get(attribute, "")) for attribute in ("class", "id", "data-role")
        ).lower()
        return any(keyword in identity for keyword in ("contact", "footer", "qrcode", "qr-code", "wechat", "weixin"))

    if tag.name not in _TEXT_TAG_NAMES:
        return False

    return is_reference_contact_text(tag.get_text(" ", strip=True))


def _find_reference_contact_region(marker: Tag) -> Tag:
    """返回可安全删除的最小联系卡容器，避免二维码误删整篇正文。"""

    if marker.name in {"aside", "footer", "figure"}:
        return marker
    if marker.name in {"div", "section"} and _is_isolated_contact_container(marker):
        # 标记本身就是联系卡时必须优先返回自身；若直接从 parents 开始，外层正文
        # section 恰好段落较少时也会被误判成孤立容器并遭到整段删除。
        return marker

    fallback = marker
    for parent in marker.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"aside", "footer", "figure"}:
            return parent
        if parent.name in {"div", "section"} and _is_isolated_contact_container(parent):
            return parent
        if parent.name == "article":
            break
    return fallback


def _is_isolated_contact_container(container: Tag) -> bool:
    """判断容器是否像独立联系卡，防止把承载全文的外层容器删除。"""

    identity = " ".join(str(container.get(attribute, "")) for attribute in ("class", "id")).lower()
    if any(keyword in identity for keyword in ("contact", "footer", "qr", "wechat", "weixin")):
        return True

    text_blocks = [
        child for child in container.find_all(_TEXT_TAG_NAMES)
        if not _has_text_block_ancestor(child)
    ]
    # 联系卡一般只有几行说明；正文容器中的多个段落不能因为其中一句“咨询”被删除。
    return len(text_blocks) <= 5 and container.find("article") is None


def _remove_reference_contact_region(image: Tag) -> None:
    """移除视觉模型识别出的二维码区域，不把固定页脚嵌入参考容器。"""

    _find_reference_contact_region(image).decompose()


def _append_configured_footer(soup: BeautifulSoup, footer_template: str) -> None:
    """仅在文章末尾追加用户任务的固定联系内容，不继承参考联系卡的视觉格式。"""

    if not footer_template.strip():
        return

    from app.services.footer_template_service import render_footer_template_html

    footer_html = render_footer_template_html(footer_template)
    if not footer_html:
        return

    footer_container = soup.new_tag("div")
    footer_container["data-ai-footer-template"] = "appended"
    fragment = BeautifulSoup(footer_html, "html.parser")
    for child in list(fragment.contents):
        footer_container.append(child)
    soup.append(footer_container)


def _text_marker(slot_id: str) -> str:
    """生成不会与用户正文自然冲突的内部文本槽位标记。"""
    return f"__AI_TEXT_SLOT_{slot_id}__"


def _image_marker(slot_id: str) -> str:
    """生成待图片 Agent 替换的内部图片地址标记。"""
    return f"__AI_IMAGE_SLOT_{slot_id}__"
