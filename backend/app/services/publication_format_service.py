"""知识库驱动的公众号发布格式服务。

品牌介绍适合向量检索，但“纯海报、图数、页脚联系方式”等发布规则属于强约束。
本模块直接按文档顺序重建完整内容并提取规则，避免规则被语义检索遗漏或被字符
上限截断；生成流程只消费结构化配置，不再猜测知识库里的版式要求。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.pg_models import KbDocumentChunk, KnowledgeBase


_SECTION_PATTERN = re.compile(r"【([^】]+)】")
_URL_PATTERN = re.compile(r"https?://[^\s，。；）)\]>'\"]+", re.IGNORECASE)
_QUOTED_TEXT_PATTERN = re.compile(r"[“\"]([^”\"]+)[”\"]")


@dataclass(frozen=True)
class PublicationFormatProfile:
    """一份可直接交给文章与图片 Agent 的发布格式强约束。"""

    is_poster_gallery: bool
    poster_count: int
    title_max_chars: int
    copy_max_chars: int
    raw_directives: str
    image_directives: str
    # 图片模型只需品牌视觉与画面规则；文章形式、正文文案和联系方式不重复传入。
    visual_directives: str
    copy_directives: str
    footer_template: str


def analyze_publication_format(document_text: str) -> PublicationFormatProfile:
    """从完整品牌文档分析发布结构，绝不删除原始格式规则。

    当前规则不依赖模型猜测：纯海报模式、图数和联系方式都使用可审计的文本规则
    提取。未识别的补充内容保留在 ``raw_directives``，由后续文案与生图提示词原样
    接收，保证新增品牌用语不会在程序升级前丢失。
    """

    raw_directives = str(document_text or "").strip()
    normalized = re.sub(r"\s+", "", raw_directives)
    is_poster_gallery = "海报拼接" in normalized and (
        "无独立文字段落" in normalized or "不需要文字" in normalized
    )
    poster_count = _extract_poster_count(raw_directives)
    title_max_chars = _extract_char_limit(raw_directives, "主标题", default=12)
    copy_max_chars = _extract_char_limit(raw_directives, "每张长图", default=60)
    image_directives = _extract_section(raw_directives, "图片要求")
    visual_directives = _join_sections(
        raw_directives,
        ("品牌调性", "图片要求", "背景要求", "视觉要求", "画面要求", "色彩要求", "材质要求", "场景要求"),
    )
    copy_directives = _extract_section(raw_directives, "文案要求")
    footer_template = _extract_footer_template(raw_directives)

    return PublicationFormatProfile(
        is_poster_gallery=is_poster_gallery,
        poster_count=poster_count,
        title_max_chars=title_max_chars,
        copy_max_chars=copy_max_chars,
        raw_directives=raw_directives,
        image_directives=image_directives,
        visual_directives=visual_directives or image_directives,
        copy_directives=copy_directives,
        footer_template=footer_template,
    )


def load_publication_format_from_knowledge_bases(
    db: Session,
    knowledge_base_ids: Iterable[int],
    tenant_id: int,
) -> PublicationFormatProfile:
    """读取任务选中的知识库全文，并构建不可截断的发布格式配置。

    文章品牌背景仍由原有检索/上下文服务提供；本函数只承担强格式规则。查询按
    文档和切片编号排序，且在相邻切片间消除固定 overlap，确保页脚 URL 与末尾规则
    能完整进入格式分析。
    """

    normalized_ids = sorted({int(item) for item in knowledge_base_ids if item})
    if not normalized_ids:
        raise ValueError("纯海报任务必须选择至少一个知识库")
    chunks = (
        db.query(KbDocumentChunk)
        .join(KnowledgeBase, KnowledgeBase.id == KbDocumentChunk.knowledge_base_id)
        .filter(
            KbDocumentChunk.knowledge_base_id.in_(normalized_ids),
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.is_active == 1,
        )
        .order_by(
            KbDocumentChunk.knowledge_base_id.asc(),
            KbDocumentChunk.document_id.asc(),
            KbDocumentChunk.chunk_index.asc(),
        )
        .all()
    )
    full_text = _merge_chunk_contents(chunk.content for chunk in chunks)
    if not full_text:
        raise ValueError("所选知识库没有可分析的发布格式内容")
    return analyze_publication_format(full_text)


def render_poster_gallery_html(
    image_urls: Iterable[str],
    footer_template: str,
) -> str:
    """渲染纯海报文章，正文仅保留图片，固定内容统一置于末尾。

    图片文案已由生图模型嵌入画面，故不可再渲染 ``p``、标题或 Markdown 文字；
    二维码不交给图片模型，只由已验证的固定页脚渲染器追加，避免模型伪造二维码。
    """

    image_nodes: list[str] = []
    for index, raw_url in enumerate(image_urls, start=1):
        image_url = str(raw_url or "").strip()
        if not image_url:
            continue
        safe_url = html.escape(image_url, quote=True)
        image_nodes.append(
            f'<img src="{safe_url}" alt="海报 {index}" '
            'style="width:100%;max-width:640px;display:block;margin:0 auto 12px;" />'
        )

    content = "\n".join(image_nodes)
    if footer_template.strip():
        from app.services.footer_template_service import render_footer_template_html

        footer_html = render_footer_template_html(footer_template)
        if footer_html:
            content += f'\n<div data-ai-footer-template="appended">{footer_html}</div>'
    return content


def _extract_section(source: str, name: str) -> str:
    """提取一个带全角方括号标题的章节，未找到时返回空字符串。"""

    matches = list(_SECTION_PATTERN.finditer(source))
    for index, match in enumerate(matches):
        if match.group(1).strip() != name:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        return source[match.end():end].strip()
    return ""


def _join_sections(source: str, names: tuple[str, ...]) -> str:
    """按原始顺序拼接指定章节，供单一职责的图片提示词使用。

    发布格式原文仍由 ``raw_directives`` 完整保留给版式和文案规划；本函数只提取
    图生图真正需要的品牌调性、背景和画面限制，减少每张图片重复传输无关内容。
    """

    allowed_names = set(names)
    matches = list(_SECTION_PATTERN.finditer(source))
    sections: list[str] = []
    for index, match in enumerate(matches):
        if match.group(1).strip() not in allowed_names:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        section = source[match.start():end].strip()
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def _extract_poster_count(source: str) -> int:
    """从“2~3 张”等规则选取成本可控的上限三张内容海报。"""

    match = re.search(r"(\d+)\s*(?:~|～|至|-)?\s*(\d+)?\s*张竖版长图", source)
    if not match:
        return 3
    lower = int(match.group(1))
    upper = int(match.group(2) or lower)
    return max(1, min(max(lower, upper), 3))


def _extract_char_limit(source: str, anchor: str, *, default: int) -> int:
    """读取“控制在 N 字”类规则；范围规则取上限并限制合理值。"""

    match = re.search(
        rf"{re.escape(anchor)}[^。\n]{{0,50}}?(?:不超过|控制在|控制为|约)?\s*(\d+)\s*(?:[-~～至]\s*(\d+))?\s*字",
        source,
    )
    if not match:
        return default
    value = int(match.group(2) or match.group(1))
    return max(8, min(value, 160))


def _extract_footer_template(source: str) -> str:
    """从末尾联系方式章节提取唯一文字和二维码 URL，拒绝额外正文。"""

    footer_source = _extract_section(source, "末尾联系方式") or source
    url_match = _URL_PATTERN.search(footer_source)
    if not url_match:
        return ""
    text_candidates = _QUOTED_TEXT_PATTERN.findall(footer_source[:url_match.start()])
    footer_text = text_candidates[-1].strip() if text_candidates else ""
    if not footer_text:
        inline_match = re.search(r"(?:显示|展示|文案)[：:\s]*([^，。\n]+TEL[^，。\n]+)", footer_source)
        footer_text = inline_match.group(1).strip() if inline_match else ""
    return f"{footer_text}\n![二维码]({url_match.group(0)})" if footer_text else ""


def _merge_chunk_contents(chunks: Iterable[str]) -> str:
    """按顺序拼接分块内容，并去掉切片器产生的相邻文本重叠。"""

    merged = ""
    for raw_chunk in chunks:
        chunk = str(raw_chunk or "").strip()
        if not chunk:
            continue
        if not merged:
            merged = chunk
            continue
        overlap = _find_overlap(merged, chunk)
        merged += chunk[overlap:]
    return merged


def _find_overlap(left: str, right: str) -> int:
    """查找前一块尾部与后一块头部的最长精确重叠，最大检查 512 字。"""

    maximum = min(len(left), len(right), 512)
    for length in range(maximum, 0, -1):
        if left[-length:] == right[:length]:
            return length
    return 0
