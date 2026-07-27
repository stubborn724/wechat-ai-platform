"""Analyze article HTML/markdown → structured block sequence → LayoutTemplate.

Two stages:
  1. ``html_to_structured_blocks`` — deterministic DOM parsing (program).
  2. ``analyze_article_layout`` — LLM semantic classification (AI).
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from bs4 import BeautifulSoup, Tag

from app.config import settings
from app.schemas.article import (
    ArticleLayoutMeta,
    LayoutBlock,
    LayoutSection,
    LayoutTemplate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage 1: DOM → structured block sequence  (deterministic, no LLM)
# ---------------------------------------------------------------------------

# Tags we keep vs skip during extraction
BLOCK_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br",
    "img", "figure",
    "blockquote",
    "ul", "ol", "li",
    "hr",
    "div", "section", "article", "main",
    "strong", "em", "b", "i", "span", "a",  # inline — handled inside blocks
}

VOID_ELEMENTS = {"br", "hr", "img", "input", "meta", "link"}


def _extract_text(el: Tag) -> str:
    """Get clean text from an element."""
    return el.get_text(separator=" ", strip=True)


def _is_qrcode_image(alt: str, src: str = "") -> bool:
    """Check if an image is likely a QR code / contact card based on alt text or URL."""
    combined = (alt + " " + src).lower()
    qr_hints = [
        "二维码", "qrcode", "qr code", "qr_code",
        "公众号", "扫码", "扫一扫", "小程序码",
        "水印", "联系", "客服", "关注",
        "wechat", "weixin",
    ]
    return any(hint in combined for hint in qr_hints)


def html_to_structured_blocks(html: str) -> List[dict]:
    """Parse article HTML into an ordered list of typed blocks.

    Returns a list of dicts, each with at least ``type`` and ``text`` keys.
    This is the deterministic "program" part — the LLM only does semantic
    classification on top of this in stage 2.
    """
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "lxml")
    blocks: List[dict] = []

    # Remove unwanted elements before traversal
    for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "noscript"]):
        tag.decompose()

    body = soup.find("body") or soup.find("article") or soup.find("main") or soup
    _traverse(body, blocks)

    # Simple heuristic: consecutive <img> or <figure> without intervening text
    # should be grouped into a single "image" block with count > 1.
    blocks = _merge_consecutive_images(blocks)

    return blocks


def _traverse(node, blocks: List[dict], depth: int = 0):
    """Recursively walk the DOM and emit typed blocks."""
    if depth > 50:  # safety: prevent runaway recursion on deeply nested DOM
        return

    for child in node.children:
        if isinstance(child, str):
            text = child.strip()
            if not text:
                continue
            # Standalone text nodes inside <body> get treated as paragraphs
            if depth <= 1 and len(text) > 20:
                blocks.append({"type": "paragraph", "text": text})
            continue

        if not isinstance(child, Tag):
            continue

        tag = child.name.lower() if child.name else ""

        # ---- Headings ----
        if re.match(r"^h[1-6]$", tag):
            level = int(tag[1])
            text = _extract_text(child)
            if text:
                blocks.append({"type": "heading", "level": level, "text": text})

        # ---- Paragraphs ----
        elif tag == "p":
            # Check for images inside <p>
            imgs = child.find_all("img")
            if imgs:
                for img in imgs:
                    src = img.get("src", img.get("data-src", "")).strip()
                    alt = img.get("alt", "").strip()
                    blocks.append({"type": "image", "src": src, "alt": alt})
                # Remaining text in <p> after images
                for img in imgs:
                    img.decompose()
                text = _extract_text(child)
                if text:
                    blocks.append({"type": "paragraph", "text": text})
            else:
                text = _extract_text(child)
                if text:
                    blocks.append({"type": "paragraph", "text": text})

        # ---- Images — skip QR codes / contact info ----
        elif tag == "img":
            src = child.get("src", child.get("data-src", "")).strip()
            if src and not src.startswith("data:"):
                alt = child.get("alt", "").strip()
                if not _is_qrcode_image(alt, src):
                    blocks.append({"type": "image", "src": src, "alt": alt})

        # ---- <figure> — might contain <img> + <figcaption> ----
        elif tag == "figure":
            img = child.find("img")
            caption = child.find("figcaption")
            if img:
                src = img.get("src", img.get("data-src", "")).strip()
                alt = img.get("alt", "").strip()
                if not _is_qrcode_image(alt, src):
                    blocks.append({"type": "image", "src": src, "alt": alt})
            if caption:
                text = _extract_text(caption)
                if text:
                    blocks.append({"type": "image_caption", "text": text})
            # If there's text outside img/caption, traverse for it
            _traverse(child, blocks, depth + 1)

        # ---- Blockquotes ----
        elif tag == "blockquote":
            text = _extract_text(child)
            if text:
                blocks.append({"type": "quote", "text": text})

        # ---- Dividers ----
        elif tag == "hr":
            blocks.append({"type": "divider", "text": ""})

        # ---- Lists ----
        elif tag in ("ul", "ol"):
            items = []
            for li in child.find_all("li", recursive=False):
                item_text = _extract_text(li)
                if item_text:
                    items.append(item_text)
            if items:
                blocks.append({"type": "list", "items": items, "ordered": tag == "ol"})

        # ---- Div / Section — recurse ----
        elif tag in ("div", "section", "article", "main", "header", "aside", "figcaption"):
            # If a <div> contains only one <img>, treat as image
            imgs = child.find_all("img", recursive=False)
            direct_text = _extract_text(child)
            if len(imgs) == 1 and not direct_text:
                src = imgs[0].get("src", imgs[0].get("data-src", "")).strip()
                alt = imgs[0].get("alt", "").strip()
                if src and not src.startswith("data:"):
                    blocks.append({"type": "image", "src": src, "alt": alt})
            else:
                _traverse(child, blocks, depth + 1)

        # ---- <br> — skip ----
        elif tag == "br":
            pass

        # ---- Inline elements inside block context — treat text as paragraph ----
        elif tag in ("strong", "em", "b", "i", "span", "a"):
            text = _extract_text(child)
            if text:
                blocks.append({"type": "paragraph", "text": text})

        # ---- Anything else — recurse ----
        else:
            _traverse(child, blocks, depth + 1)


def _merge_consecutive_images(blocks: List[dict]) -> List[dict]:
    """Merge consecutive image blocks into a single block with count > 1."""
    if not blocks:
        return blocks
    merged = []
    i = 0
    while i < len(blocks):
        if blocks[i]["type"] == "image":
            count = 1
            srcs = [blocks[i].get("src", "")]
            alts = [blocks[i].get("alt", "")]
            j = i + 1
            while j < len(blocks) and blocks[j]["type"] == "image":
                count += 1
                srcs.append(blocks[j].get("src", ""))
                alts.append(blocks[j].get("alt", ""))
                j += 1
            merged.append({
                "type": "image",
                "count": count,
                "srcs": srcs,
                "alts": alts,
            })
            i = j
        else:
            merged.append(blocks[i])
            i += 1
    return merged


# ---------------------------------------------------------------------------
# Helper: build a normalized content hash for freshness checks
# ---------------------------------------------------------------------------

def _content_hash(markdown: str) -> str:
    normalized = re.sub(r"\s+", " ", markdown or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 2: LLM semantic classification → LayoutTemplate
# ---------------------------------------------------------------------------

LAYOUT_ANALYSIS_PROMPT = """你是一个专业的版式结构分析专家。分析下面的内容块序列，输出文章的结构模板。

你的任务：
1. 将连续的 blocks 归类为「章节」（section），每个 section 有自己的语义角色
2. 识别每个 content block 的「语义角色」（role）
3. 判断结尾风格
4. 记录整体版式特征

要求：
- section_role 用英文简短描述，如 "opening" / "selling_point_1" / "closing"
- block.role 用英文描述语义功能，如 "scene_setting" / "feature_explain" / "usage_scenario"
- 不要直接复制原文标题作为 section_role，而是描述「这一节在文中起什么作用」
- title_pattern 用于保存标题句式结构（如 "从{A}到{B}：{C}"），不要复制原文措辞
- length_chars_target 根据实际字符数计算，目标值取中位数

输出严格的 JSON 格式，不要其他文字：

```json
{
  "sections": [
    {
      "section_role": "string",
      "blocks": [
        {
          "type": "heading|paragraph|image|quote|...",
          "role": "string",
          "level": 2,
          "length_chars_target": 100,
          "count": 1,
          "style_pattern": "从{A}到{B}：{C}"
        }
      ]
    }
  ],
  "ending_style": "summary|interaction|emotional_summary|call_to_action",
  "total_paragraph_count": 0,
  "total_image_count": 0,
  "layout_features": ["feature1", "feature2"]
}
```"""


async def analyze_article_layout(
    blocks: List[dict],
    markdown: str = "",
    title: str = "",
) -> ArticleLayoutMeta:
    """Call LLM to classify structured blocks → LayoutTemplate.

    Returns an ``ArticleLayoutMeta`` wrapper ready to store in ``FeedSourceArticle.analysis``.
    """
    from openai import AsyncOpenAI

    content_hash = _content_hash(markdown)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Fallback if no blocks
    if not blocks:
        return ArticleLayoutMeta(
            layout_status="failed",
            layout_error="No structured blocks extracted from HTML",
            source_content_hash=content_hash,
            analyzed_at=now_iso,
            analysis_meta={"model": "", "prompt_version": "layout-v1"},
        )

    # Build the block sequence text for the LLM
    # We strip image src for privacy/token-efficiency and keep only type + role hints
    block_lines = []
    for i, b in enumerate(blocks):
        t = b["type"]
        if t == "heading":
            block_lines.append(f"  [{i}] heading (h{b.get('level', 2)}): {b.get('text', '')[:100]}")
        elif t == "paragraph":
            text = b.get("text", "")
            block_lines.append(f"  [{i}] paragraph ({len(text)} chars): {text[:100]}")
        elif t == "image":
            count = b.get("count", 1)
            alt = b.get("alt", b.get("alts", [""])[0])[:60]
            block_lines.append(f"  [{i}] image x{count}  alt=\"{alt}\"")
        elif t == "image_caption":
            block_lines.append(f"  [{i}] image_caption: {b.get('text', '')[:80]}")
        elif t == "quote":
            block_lines.append(f"  [{i}] quote: {b.get('text', '')[:80]}")
        elif t == "divider":
            block_lines.append(f"  [{i}] divider")
        elif t == "list":
            items = b.get("items", [])
            block_lines.append(f"  [{i}] list ({len(items)} items): {items[0][:60] if items else ''}")
        else:
            block_lines.append(f"  [{i}] {t}: {str(b)[:80]}")

    block_text = "\n".join(block_lines)
    total_chars = len(block_text)

    # Truncate if too long (rough token limit)
    if total_chars > 8000:
        block_text = block_text[:8000] + "\n  ... (truncated)"

    prompt = f"""标题：{title or "(无)"}

内容块序列（共 {len(blocks)} 个块）：
{block_text}

请分析这个文章的结构模板，输出 JSON。"""

    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.dashscope_model,
            messages=[
                {"role": "system", "content": LAYOUT_ANALYSIS_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content or "{}"
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        data = json.loads(raw)
        template = LayoutTemplate(**data)

        return ArticleLayoutMeta(
            layout_status="completed",
            layout_template=template,
            source_content_hash=content_hash,
            analyzed_at=now_iso,
            analysis_meta={
                "model": settings.dashscope_model,
                "prompt_version": "layout-v1",
                "blocks_count": len(blocks),
            },
        )

    except Exception as exc:
        logger.warning("Layout analysis failed: %s", exc)
        return ArticleLayoutMeta(
            layout_status="failed",
            layout_error=str(exc)[:500],
            source_content_hash=content_hash,
            analyzed_at=now_iso,
            analysis_meta={"model": settings.dashscope_model, "prompt_version": "layout-v1"},
        )


# ---------------------------------------------------------------------------
# Convenience: full pipeline from HTML → saved ArticleLayoutMeta
# ---------------------------------------------------------------------------

async def analyze_feed_article_layout(
    html: str,
    markdown: str = "",
    title: str = "",
) -> ArticleLayoutMeta:
    """One-shot: HTML → structured blocks → LLM classification → LayoutTemplate.

    Wraps the result in ``ArticleLayoutMeta`` ready to store.
    """
    blocks = html_to_structured_blocks(html)
    meta = await analyze_article_layout(blocks, markdown=markdown, title=title)
    return meta


# ---------------------------------------------------------------------------
# Layout validator: check generated article against LayoutTemplate
# ---------------------------------------------------------------------------

def validate_article_against_layout(
    markdown: str,
    template: LayoutTemplate,
) -> dict:
    """Compare a generated article (markdown) against the reference layout template.

    Returns a dict with:
      ``valid`` (bool): whether all hard constraints pass.
      ``errors`` (list[str]): specific violations found.
      ``stats`` (dict): section/paragraph/image counts for reference.
    """
    errors: list[str] = []

    # Count structural elements in the generated content
    import re as _re

    # Headings (both HTML and markdown)
    headings_found = len(_re.findall(r'<h[1-6][^>]*>|^##+|^#\s', markdown, _re.MULTILINE))
    sections_found = headings_found

    # Images (both HTML and markdown)
    images_found = len(_re.findall(r'<img[^>]+>|!\[.*?\]\(', markdown))

    # Paragraphs (approximate: text blocks separated by blank lines)
    blocks = [b.strip() for b in markdown.split('\n\n') if b.strip()]
    paragraphs_found = sum(
        1 for b in blocks
        if not b.startswith('<') and not b.startswith('#')
        and not b.startswith('![') and not b.startswith('<img')
    )

    # --- Hard constraints ---
    expected_sections = len(template.sections)
    if sections_found < expected_sections:
        # Relaxed: allow missing sections but warn
        errors.append(
            f"Section count mismatch: expected ~{expected_sections}, got {sections_found}"
        )

    expected_images = template.total_image_count
    if expected_images > 0 and images_found < expected_images * 0.5:
        errors.append(
            f"Image count too low: expected ~{expected_images}, got {images_found}"
        )

    # Check ending style hint
    ending_lines = [l.strip() for l in markdown.split('\n')[-5:] if l.strip()]
    if template.ending_style and ending_lines:
        ending_text = " ".join(ending_lines[-3:])
        # Simple heuristic: if ending should be emotional but last lines are generic
        # (this is a soft check — just log, don't error)
        if template.ending_style == "interaction" and "?" not in ending_text and "?" not in ending_text:
            errors.append(
                f"Ending style: expected '{template.ending_style}', but no question found in last 5 lines"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "stats": {
            "sections": sections_found,
            "expected_sections": expected_sections,
            "paragraphs": paragraphs_found,
            "images": images_found,
            "expected_images": expected_images,
            "headings": headings_found,
        },
    }
