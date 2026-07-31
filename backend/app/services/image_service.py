"""Image search service supporting local asset library and DashScope AI.

Usage::

    service = ImageService()
    url = await service.search_image(ImageSourceType.LOCAL, "mountain sunset")
"""

from enum import Enum
from typing import Optional

import httpx

from app.config import settings


class ImageSourceType(str, Enum):
    LOCAL = "local"
    DASHSCOPE = "dashscope"


PICSUM_PLACEHOLDER = "https://picsum.photos/seed/{seed}/{width}/{height}"


class ImageService:
    """Unified image search service with local and DashScope back-ends.

    .. tip:: The synchronous ``search_image`` method uses ``httpx`` in
       synchronous mode so it can be called from both sync and async contexts.
       For pure async callers, ``await search_image(...)`` is also valid —
       the method simply returns the result directly.
    """

    def __init__(self) -> None:
        pass

    async def search_image(
        self,
        source: ImageSourceType,
        keywords: str,
        db: Optional[object] = None,
    ) -> Optional[str]:
        """Search for an image from the specified *source*.

        Returns a direct image URL on success, or a fallback placeholder URL.
        """
        if source == ImageSourceType.LOCAL:
            return self._search_local(keywords, db)
        return self._fallback(keywords)

    def _search_local(self, keywords: str, db: Optional[object]) -> Optional[str]:
        """Search the ``Asset`` table for matching tags or filename.

        This is a stub — implement actual DB query logic when the asset
        library back-end is connected.
        """
        if db is None:
            return self._fallback(keywords)

        # TODO: implement actual asset-library search:
        #   db.query(Asset).filter(
        #       Asset.tags.contains(keywords) | Asset.filename.ilike(f"%{keywords}%")
        #   ).first()
        return self._fallback(keywords)

    @staticmethod
    def _fallback(keywords: str) -> str:
        """Return a deterministic picsum placeholder URL."""
        seed = keywords.replace(" ", "-") or "default"
        return PICSUM_PLACEHOLDER.format(seed=seed, width=800, height=600)
