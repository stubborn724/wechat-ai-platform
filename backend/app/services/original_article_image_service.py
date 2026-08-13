"""原创图文正文配图的确定性编排服务。

她格属于知识库驱动的原创图文，而非连续海报。图片 Agent 已能生成与文章段落
相关的图片需求，但普通 Markdown 内容没有 ``[IMAGE:]`` 占位符时，旧合并逻辑
无法把图片写回正文。本模块只负责将已有图片按章节锚点嵌入正文，不参与模型调用，
从而把“图片生成”和“文章排版”保持为可独立测试的职责。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
import re

from app.schemas.article import ImageResult
from app.services.writing_style_template_service import (
    SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
)


@dataclass(frozen=True)
class _MarkdownSection:
    """Markdown 主章节的位置快照。

    只记录 H2 及以下标题，避免把文章主标题当作一张没有业务段落的封面图位置。
    ``line_index`` 指向标题行，渲染时在该行之后插入图片，保持段落语义和阅读顺序。
    """

    title: str
    line_index: int


def should_insert_original_article_images(style: str | None) -> bool:
    """判断任务是否应采用原创图文正文配图。

    这个开关必须精确限定为她格模板。剪纸、写怀、中西无界走程序化三图海报，
    绣蔓和历史仿写任务继续使用原有图片策略，避免一次排版优化改变生产链路。
    """

    return (style or "").strip().lower() == SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID


def append_shege_image_requirement_context(
    prompt: str,
    *,
    style: str | None,
    image_prompt_context: str | None,
) -> str:
    """为她格图片需求分析补充知识库视觉边界。

    图片需求 Agent 同时看到文章正文和知识库中提取的图片规则，才能把“客户分群”
    “库存预测”等章节生成相应经营场景，而不是泛化成不相关的科技办公图。非她格
    任务原样返回，确保现有任务的 token 消耗和提示词不发生变化。
    """

    if not should_insert_original_article_images(style):
        return prompt

    normalized_context = (image_prompt_context or "").strip()
    if not normalized_context:
        return prompt + (
            "\n\n## 她格原创图文配图要求\n"
            "每张图片必须对应正文中一个具体经营章节，呈现真实可落地的中小企业业务场景。"
        )

    return prompt + (
        "\n\n## 她格知识库图片规则\n"
        f"{normalized_context[:4000]}\n\n"
        "## 章节关联硬约束\n"
        "每项图片需求必须对应章节中的一个具体业务段落：section_title 要与正文小标题一致或高度接近；"
        "prompt 必须复述该章节的业务动作、使用对象和真实经营场景，禁止只输出泛化的 AI、"
        "芯片、抽象数据流或无关办公环境。"
    )


def insert_original_article_images(
    content: str,
    images: Sequence[ImageResult],
) -> str:
    """把成功生成的原创图文图片插入对应正文章节。

    优先按图片需求中的 ``section_title`` 匹配 Markdown 小标题；模型偶尔会返回概括
    标题时，则按图片和章节的自然顺序回退。该规则不再次请求模型，既能保证图片可见，
    又避免为排版额外消耗 token。函数幂等地跳过正文中已有 URL，支持重试时复用结果。
    """

    normalized_content = str(content or "")
    valid_images = [
        image for image in images
        if str(getattr(image, "url", "") or "").strip()
        and str(getattr(image, "url", "") or "").strip() not in normalized_content
    ]
    if not normalized_content or not valid_images:
        return normalized_content

    if normalized_content.lstrip().startswith("<"):
        return _insert_images_into_html(normalized_content, valid_images)
    return _insert_images_into_markdown(normalized_content, valid_images)


def _insert_images_into_markdown(content: str, images: Sequence[ImageResult]) -> str:
    """在 Markdown H2/H3 标题之后注入图片节点。"""

    lines = content.splitlines()
    sections = _find_markdown_sections(lines)
    if not sections:
        # 没有章节标题时不能凭空拆散正文，统一在正文末尾追加，仍保证图片不会丢失。
        return content.rstrip() + "\n\n" + "\n\n".join(
            _build_image_html(image, "文章配图") for image in images
        )

    images_by_line: dict[int, list[ImageResult]] = defaultdict(list)
    used_line_indexes: set[int] = set()
    fallback_section_index = 0
    for image in images:
        target = _match_section(image, sections, used_line_indexes)
        if target is None:
            target = sections[min(fallback_section_index, len(sections) - 1)]
            fallback_section_index += 1
        used_line_indexes.add(target.line_index)
        images_by_line[target.line_index].append(image)

    rendered_lines: list[str] = []
    for line_index, line in enumerate(lines):
        rendered_lines.append(line)
        for image in images_by_line.get(line_index, []):
            rendered_lines.extend(("", _build_image_html(image, _heading_text(line)), ""))
    return "\n".join(rendered_lines).strip()


def _insert_images_into_html(content: str, images: Sequence[ImageResult]) -> str:
    """为 HTML 正文提供与 Markdown 一致的章节级回填能力。"""

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    heading_nodes = list(soup.find_all(["h2", "h3", "h4", "h5", "h6"]))
    if not heading_nodes:
        for image in images:
            soup.append(BeautifulSoup(_build_image_html(image, "文章配图"), "html.parser"))
        return str(soup)

    used_indexes: set[int] = set()
    fallback_index = 0
    for image in images:
        heading_index = _match_html_heading(image, heading_nodes, used_indexes)
        if heading_index is None:
            heading_index = min(fallback_index, len(heading_nodes) - 1)
            fallback_index += 1
        used_indexes.add(heading_index)
        heading_nodes[heading_index].insert_after(
            BeautifulSoup(_build_image_html(image, heading_nodes[heading_index].get_text(" ", strip=True)), "html.parser")
        )
    return str(soup)


def _find_markdown_sections(lines: Sequence[str]) -> list[_MarkdownSection]:
    """提取可承接正文图片的 Markdown 或普通文本小标题。

    内容模型在未使用 HTML 模板的任务中可能输出 ``## 标题``，也可能输出末尾带两个
    空格的普通标题行。后者是 Markdown 的硬换行写法，且常见于模型生成的中文正文。
    只把“不以句末标点结束且长度受控”的硬换行行识别为标题，避免把普通段落误当作
    图片锚点并破坏阅读顺序。
    """

    sections: list[_MarkdownSection] = []
    for line_index, line in enumerate(lines):
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if match:
            sections.append(_MarkdownSection(title=match.group(1), line_index=line_index))
            continue
        plain_title = _plain_text_heading(line)
        if plain_title:
            sections.append(_MarkdownSection(title=plain_title, line_index=line_index))
    return sections


def _plain_text_heading(line: str) -> str:
    """识别模型输出的普通文本标题行。

    仅接受两个以上尾随空格形成的硬换行，且标题不能以中文句末标点结束。这个组合
    与本次她格文章中“标题 + 两空格 + 正文”的形态一致，也能避免把每个正常段落
    都纳入章节候选。
    """

    if not re.search(r"\s{2,}$", line):
        return ""
    normalized = line.strip()
    if not normalized or len(normalized) > 64:
        return ""
    if normalized.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
        return ""
    return normalized


def _match_section(
    image: ImageResult,
    sections: Sequence[_MarkdownSection],
    used_line_indexes: set[int],
) -> _MarkdownSection | None:
    """按图片需求标题匹配正文标题，避免图片落到不相关段落。"""

    target_title = _normalize_title(getattr(image, "section_title", ""))
    if not target_title:
        return None
    for section in sections:
        section_title = _normalize_title(section.title)
        if section.line_index not in used_line_indexes and (
            target_title in section_title or section_title in target_title
        ):
            return section
    return None


def _match_html_heading(
    image: ImageResult,
    headings: Sequence[object],
    used_indexes: set[int],
) -> int | None:
    """在 HTML 标题节点中复用与 Markdown 相同的语义匹配规则。"""

    target_title = _normalize_title(getattr(image, "section_title", ""))
    if not target_title:
        return None
    for heading_index, heading in enumerate(headings):
        heading_title = _normalize_title(heading.get_text(" ", strip=True))
        if heading_index not in used_indexes and (
            target_title in heading_title or heading_title in target_title
        ):
            return heading_index
    return None


def _build_image_html(image: ImageResult, fallback_alt: str) -> str:
    """构建安全、可发布的正文图片节点，不让模型直接拼接 HTML。"""

    url = escape(str(image.url).strip(), quote=True)
    alt = escape(str(getattr(image, "section_title", "") or fallback_alt).strip(), quote=True)
    return (
        '<figure data-ai-original-image="shege" style="margin:20px 0;text-align:center;">'
        f'<img src="{url}" alt="{alt}" style="width:100%;max-width:720px;display:block;margin:0 auto;" />'
        "</figure>"
    )


def _heading_text(line: str) -> str:
    """从 Markdown 标题行取出图片替代文本。"""

    return re.sub(r"^#+\s*", "", line).strip() or "文章配图"


def _normalize_title(value: object) -> str:
    """标准化标题，容忍模型输出中的标点和空白差异。"""

    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(value or "")).lower()
