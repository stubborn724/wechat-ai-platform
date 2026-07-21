"""LangGraph node for parallel image generation (Agent 5).

Generates or fetches images for each ``ImageRequirement`` in the state.
Uses ``ImageServiceStrategy`` from ``app.services`` when available, with
fallback to direct Pexels API or local asset search.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from app.agent.state import ArticleGenState
from app.schemas.article import ImageRequirement, ImageResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attempt to import the production image-service strategy
# ---------------------------------------------------------------------------
try:
    from app.services import ImageServiceStrategy

    _HAS_IMAGE_SERVICE = True
except ImportError:
    _HAS_IMAGE_SERVICE = False
    logger.info("ImageServiceStrategy not available; using direct fallback")


def _resolve_image_single(
    req: Dict,
    image_source: str,
) -> Optional[ImageResult]:
    """Resolve a single image requirement.

    Args:
        req: Serialised ImageRequirement dict.
        image_source: Global image source preference ("pexels" or "local").

    Returns:
        An ImageResult if successful, or None.
    """
    requirement = ImageRequirement(**req)
    method = requirement.image_source.lower() or image_source

    try:
        if _HAS_IMAGE_SERVICE:
            strategy = ImageServiceStrategy()
            url = strategy.execute(requirement)
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

        # Fallback: if we have Pexels API configured, use it
        from app.config import settings

        if method == "pexels" and settings.pexels_api_key:
            url = _fetch_from_pexels(requirement.keywords)
            if url:
                return ImageResult(
                    position=requirement.position,
                    url=url,
                    method="pexels",
                    keywords=requirement.keywords,
                    section_title=requirement.section_title,
                    description=requirement.prompt,
                    placeholder_id=requirement.placeholder_id,
                )

        # Last resort — local placeholder
        placeholder_url = _local_placeholder(requirement)
        if placeholder_url:
            return ImageResult(
                position=requirement.position,
                url=placeholder_url,
                method="placeholder",
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


def _fetch_from_pexels(keywords: str) -> Optional[str]:
    """Fetch the first Pexels image URL for the given keywords.

    This is a minimal inline fallback.  The full implementation should live
    in ``app.services`` or ``app.integrations``.
    """
    import os

    try:
        import requests
    except ImportError:
        logger.warning("requests not installed; skipping Pexels fallback")
        return None

    from app.config import settings

    api_key = settings.pexels_api_key
    if not api_key:
        return None

    resp = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": keywords, "per_page": 1},
        headers={"Authorization": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    photos = data.get("photos", [])
    if photos:
        return photos[0]["src"]["medium"]
    return None


def _local_placeholder(requirement: ImageRequirement) -> Optional[str]:
    """Return a local placeholder SVG data-URI when no image can be fetched."""
    from app.config import settings

    # Attempt to use MinIO / local asset if configured
    if settings.minio_endpoint and settings.minio_bucket:
        return (
            f"{settings.minio_endpoint}/{settings.minio_bucket}"
            f"/placeholders/{requirement.type}.svg"
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

    image_source = state.get("image_source", "pexels")
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
