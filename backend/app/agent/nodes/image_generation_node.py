"""LangGraph node for parallel image generation (Agent 5).

Generates or fetches images for each ``ImageRequirement`` in the state.
Uses the production ``ImageServiceStrategy`` and preserves the structured
prompt prepared by upstream agents.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from app.agent.state import ArticleGenState
from app.schemas.article import ImageRequirement, ImageResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
from app.services.image_service_v2 import ImageServiceStrategy


def _resolve_image_single(
    req: Dict,
    image_source: str,
) -> Optional[ImageResult]:
    """Resolve a single image requirement.

    Args:
        req: Serialised ImageRequirement dict.
        image_source: Global image source preference ("DASHSCOPE" or "local").

    Returns:
        An ImageResult if successful, or None.
    """
    requirement = ImageRequirement(**req)
    method = (requirement.image_source or image_source or "DASHSCOPE").upper()

    try:
        strategy = ImageServiceStrategy()
        # 节点由线程池调用，因此此处没有运行中的事件循环。显式执行异步策略并把
        # 上游已合成的视觉提示词透传给万相，禁止退回到仅关键词生图。
        url = asyncio.run(
            strategy.execute(method, requirement.keywords, prompt=requirement.prompt)
        )
        if url:
            return ImageResult(
                position=requirement.position,
                url=url,
                method=method,
                keywords=requirement.keywords,
                section_title=requirement.section_title,
                description=requirement.prompt,
                placeholder_id=requirement.placeholder_id,
            )

        logger.warning("No image resolved for position %s", requirement.position)
        return None

    except Exception as exc:
        logger.error(
            "Failed to resolve image for position %s: %s",
            requirement.position,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


def generate_images_node(state: ArticleGenState) -> dict:
    """生成图片（并行）。

    Iterates over all ``image_requirements`` in the state and resolves each
    one in parallel using a thread pool.  Results are stored in ``images``.
    """
    requirements = state.get("image_requirements", [])
    if not requirements:
        logger.info("No image requirements to process")
        return {"images": []}

    image_source = state.get("image_source", "DASHSCOPE")
    logger.info("Generating %d images (source=%s)", len(requirements), image_source)

    images: List[ImageResult] = []
    with ThreadPoolExecutor(max_workers=min(len(requirements), 5)) as pool:
        fut_map = {
            pool.submit(_resolve_image_single, req, image_source): req
            for req in requirements
        }
        for future in as_completed(fut_map):
            result = future.result()
            if result is not None:
                images.append(result)

    # Sort by position to maintain order
    images.sort(key=lambda img: img.position)

    logger.info(
        "Resolved %d / %d images", len(images), len(requirements)
    )
    return {"images": [img.model_dump() for img in images]}
