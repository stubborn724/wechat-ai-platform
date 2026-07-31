"""投喂参考图片的统一分析服务。

图文仿写、纯图片仿写和定时任务都需要从参考内容中提取图片、调用视觉理解
Agent，并在二维码场景下停止仿写。该服务只负责这些可复用的确定性编排：不访问
数据库、不生成图片，也不决定文章如何发布，从而让各入口共享相同的安全边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence


_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")


@dataclass(frozen=True)
class ReferenceImageAnalysis:
    """一张允许仿写的参考图片及其视觉理解结果。

    ``source_index`` 记录它在原始图片列表中的位置。二维码被过滤后仍保留这个
    索引，可以防止后续 HTML 槽位或图片地址出现错位绑定。
    """

    source_url: str
    source_index: int
    description: dict


@dataclass(frozen=True)
class ReferenceMediaAnalysisResult:
    """一次参考图片分析的可用结果与二维码过滤信息。"""

    usable_images: tuple[ReferenceImageAnalysis, ...]
    skipped_qrcode_count: int
    skipped_qrcode_source_indexes: tuple[int, ...]


def extract_markdown_image_urls(markdown: str) -> list[str]:
    """按出现顺序提取 Markdown 图片地址。

    仅返回图片 URL，不解析文章文字。图片地址顺序是后续视觉描述与页面槽位绑定的
    唯一稳定依据，因此不能使用集合去重或排序。
    """
    return [match.group(1).strip() for match in _MARKDOWN_IMAGE_PATTERN.finditer(markdown or "")]


def analyze_markdown_reference_images(
    markdown: str,
    understand_images_fn: Callable[[list[str]], list[dict]],
) -> ReferenceMediaAnalysisResult:
    """提取 Markdown 图片后执行统一的视觉理解与二维码过滤。"""
    return analyze_reference_images(
        extract_markdown_image_urls(markdown),
        understand_images_fn,
    )


def analyze_reference_images(
    image_urls: Sequence[str],
    understand_images_fn: Callable[[list[str]], list[dict]],
) -> ReferenceMediaAnalysisResult:
    """分析参考图片，并明确将二维码排除在仿写范围外。

    视觉理解 Agent 按输入顺序返回描述。本函数以索引进行绑定，不依赖模型可能返回的
    ``image_index`` 字段；当模型未返回某张图片的有效描述时，该图片同样不会进入生成
    队列，避免以空描述误生成不相关素材。
    """
    normalized_urls = [str(url or "").strip() for url in image_urls]
    if not normalized_urls:
        return ReferenceMediaAnalysisResult((), 0, ())

    descriptions = understand_images_fn(normalized_urls) or []
    usable_images: list[ReferenceImageAnalysis] = []
    skipped_qrcode_source_indexes: list[int] = []

    for source_index, source_url in enumerate(normalized_urls):
        description = descriptions[source_index] if source_index < len(descriptions) else None
        if not isinstance(description, dict):
            continue
        if description.get("is_qrcode") is True:
            skipped_qrcode_source_indexes.append(source_index)
            continue
        usable_images.append(
            ReferenceImageAnalysis(
                source_url=source_url,
                source_index=source_index,
                description=description,
            )
        )

    return ReferenceMediaAnalysisResult(
        usable_images=tuple(usable_images),
        skipped_qrcode_count=len(skipped_qrcode_source_indexes),
        skipped_qrcode_source_indexes=tuple(skipped_qrcode_source_indexes),
    )
