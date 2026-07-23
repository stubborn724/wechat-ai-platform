"""LangGraph node for merging images into the final article content.

Replaces ``[IMAGE:...]`` placeholders with Markdown image syntax
``![alt](url)`` and assembles the final ``full_content`` string.
"""

import logging
import re
from typing import Dict, List

from app.agent.state import ArticleGenState
from app.schemas.article import ImageResult

logger = logging.getLogger(__name__)

# Regex matching placeholders inserted by Agent 3 or elsewhere
_PLACEHOLDER_RE = re.compile(
    r"\[IMAGE:\s*position=(\d+)\s*[^\]]*\]"
)


def _build_image_lookup(images: List[Dict]) -> Dict[int, ImageResult]:
    """Build a ``{position: ImageResult}`` lookup from the serialised images."""
    lookup: Dict[int, ImageResult] = {}
    for img in images:
        if isinstance(img, dict):
            pos = img.get("position", 0)
            lookup[pos] = ImageResult(**img)
        else:
            lookup[img.position] = img
    return lookup


def merge_content_node(state: ArticleGenState) -> dict:
    """合并图片到文章内容。

    Scans the article ``content`` for ``[IMAGE:...]`` placeholders and
    replaces each one with a Markdown image tag using the corresponding
    entry in ``images``.  The final merged result is stored in
    ``full_content``.

    If no placeholders are found, images are appended at the end of the
    content as a gallery.

    Post-processing:
    - Removes any unreplaced ``[IMAGE:...]`` placeholders
    - Normalizes excessive blank lines (4+ → 2)
    """
    content = state.get("content", "")
    images = state.get("images", [])

    if not content:
        logger.warning("No content to merge images into")
        return {"full_content": content}

    if not images:
        logger.info("No images to merge; returning content as-is")
        merged = content
    else:
        lookup = _build_image_lookup(images)
        placeholders = _PLACEHOLDER_RE.findall(content)

        if placeholders:
            logger.info("Replacing %d image placeholders", len(placeholders))

            def _replace_match(match: re.Match) -> str:
                pos = int(match.group(1))
                img = lookup.get(pos)
                if img is None:
                    logger.debug("No image found for position %d; removing placeholder", pos)
                    return ""
                alt_text = img.keywords or f"Image {pos}"
                return (
                    f'{alt_text}\n\n'
                    f'<img src="{img.url}" alt="{alt_text}" '
                    f'style="width:100%;max-width:640px;border-radius:8px;display:block;margin:16px auto;" />'
                )

            merged = _PLACEHOLDER_RE.sub(_replace_match, content)
        else:
            logger.info(
                "No placeholders found; appending %d images to end of content",
                len(images),
            )
            merged = content + "\n\n"
            for img in images:
                alt = img.keywords or f"Image {img.position}"
                merged += (
                    f'<img src="{img.url}" alt="{alt}" '
                    f'style="width:100%;max-width:640px;border-radius:8px;display:block;margin:16px auto;" />\n\n'
                )

    # Post-processing: remove any remaining [IMAGE:] placeholders
    merged = re.sub(r'\[IMAGE:[^\]]*\]', '', merged)

    # Post-processing: normalize excessive blank lines (4+ → 2)
    merged = re.sub(r'\n{4,}', '\n\n', merged)

    # Post-processing: normalize 3+ spaces at line starts
    merged = re.sub(r'^ {3,}', '', merged, flags=re.MULTILINE)

    # Post-processing: 去掉正文开头的标题（已由前端独立展示）
    selected_title = state.get("selected_title")
    main_title = None
    if selected_title:
        if isinstance(selected_title, dict):
            main_title = selected_title.get("main_title", "")
        else:
            main_title = getattr(selected_title, "main_title", "")
    if main_title:
        lines = merged.split("\n")
        while lines:
            stripped = lines[0].strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                title_text = stripped.lstrip("#").strip()
                if title_text == main_title:
                    lines.pop(0)
                    continue
            elif not stripped:
                lines.pop(0)
                continue
            break
        merged = "\n".join(lines)

    # Append footer template if configured（不加横线）
    footer_template = state.get("footer_template", "")
    if footer_template:
        merged = f"{merged}\n\n{footer_template.strip()}"

    logger.info("Merged content length: %d characters", len(merged))
    return {"full_content": merged}
