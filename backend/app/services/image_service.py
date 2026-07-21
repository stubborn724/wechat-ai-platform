"""Image search service supporting multiple sources (local asset library, Pexels).

Usage::

    service = ImageService()
    url = await service.search_image(ImageSourceType.PEXELS, "mountain sunset")
"""

from enum import Enum
from typing import Optional

import httpx

from app.config import settings


class ImageSourceType(str, Enum):
    LOCAL = "local"
    PEXELS = "pexels"


PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_PER_PAGE = 5
PICSUM_PLACEHOLDER = "https://picsum.photos/seed/{seed}/{width}/{height}"


class ImageService:
    """Unified image search service with local and Pexels back-ends.

    .. tip:: The synchronous ``search_image`` method uses ``httpx`` in
       synchronous mode so it can be called from both sync and async contexts.
       For pure async callers, ``await search_image(...)`` is also valid —
       the method simply returns the result directly.
    """

    def __init__(self) -> None:
        self.pexels_api_key = settings.pexels_api_key

    async def search_image(
        self,
        source: ImageSourceType,
        keywords: str,
        db: Optional[object] = None,
    ) -> Optional[str]:
        """Search for an image from the specified *source*.

        Returns a direct image URL on success, or a fallback placeholder URL.
        """
        if source == ImageSourceType.PEXELS:
            return await self._search_pexels(keywords)
        elif source == ImageSourceType.LOCAL:
            return self._search_local(keywords, db)
        return self._fallback(keywords)

    async def _search_pexels(self, keywords: str) -> Optional[str]:
        """Call the Pexels ``/v1/search`` endpoint and return the first
        medium-sized photo URL."""
        if not self.pexels_api_key:
            return self._fallback(keywords)

        headers = {"Authorization": self.pexels_api_key}
        params = {"query": keywords, "per_page": PEXELS_PER_PAGE}

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    PEXELS_SEARCH_URL, headers=headers, params=params, timeout=15.0
                )
                resp.raise_for_status()
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    return photos[0]["src"]["medium"]
            except httpx.HTTPError:
                pass

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
