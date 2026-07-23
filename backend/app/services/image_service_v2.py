"""Strategy-based image search services (adapted from ai-passage-creator).

Provides pluggable image search back-ends that can be registered with
:class:`ImageServiceStrategy` and dispatched by method name.
"""

import abc
from typing import List, Optional

import httpx

from app.config import settings

# ---------------------------------------------------------------------------
# Base abstraction
# ---------------------------------------------------------------------------


class ImageSearchService(abc.ABC):
    """Abstract base for an image-search back-end."""

    @abc.abstractmethod
    async def search_image(self, keywords: str, **kwargs) -> Optional[str]:
        """Search for an image matching *keywords*.

        Returns a direct image URL or ``None``.
        """
        ...

    @abc.abstractmethod
    def get_method(self) -> str:
        """Return the unique method identifier for this service (e.g. ``"PEXELS"``)."""
        ...

    @staticmethod
    def get_fallback_image(keywords: str) -> str:
        """Return a deterministic picsum placeholder."""
        seed = keywords.replace(" ", "-") or "default"
        return f"https://picsum.photos/seed/{seed}/800/600"


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class PexelsService(ImageSearchService):
    """Search images via the Pexels public API."""

    SEARCH_URL = "https://api.pexels.com/v1/search"
    PER_PAGE = 5

    def __init__(self) -> None:
        self.api_key = settings.pexels_api_key

    def get_method(self) -> str:
        return "PEXELS"

    async def search_image(self, keywords: str, **kwargs) -> Optional[str]:
        if not self.api_key:
            return self.get_fallback_image(keywords)

        headers = {"Authorization": self.api_key}
        params = {"query": keywords, "per_page": self.PER_PAGE}

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    self.SEARCH_URL, headers=headers, params=params, timeout=15.0
                )
                resp.raise_for_status()
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    return photos[0]["src"]["medium"]
            except httpx.HTTPError:
                pass

        return self.get_fallback_image(keywords)


# ---------------------------------------------------------------------------
# Strategy registry / dispatcher
# ---------------------------------------------------------------------------


class DashScopeImageGenService(ImageSearchService):
    """Generate images via DashScope Wanxiang (通义万相) API.

    Uses the prompt from ImageRequirement.prompt as the generation prompt.
    """

    def __init__(self) -> None:
        from app.services.wanxiang_service import WanxiangImageService
        self.wanxiang = WanxiangImageService()

    def get_method(self) -> str:
        return "DASHSCOPE"

    async def search_image(self, keywords: str, **kwargs) -> Optional[str]:
        prompt = kwargs.get("prompt") or keywords
        if not prompt:
            return self.get_fallback_image(keywords)
        # 默认不生成文字，除非 prompt 中明确要求了
        url = await self.wanxiang.generate_image(prompt, no_text=True)
        if url:
            return url
        # Fallback to Pexels if Wanxiang fails
        return self.get_fallback_image(keywords)


class ImageServiceStrategy:
    """Registry of image-search strategies dispatched by method name.

    Usage::

        strategy = ImageServiceStrategy()
        url = await strategy.execute("PEXELS", "mountain sunset")
    """

    def __init__(self) -> None:
        self._services: dict = {}

        # Register built-in services
        pexels = PexelsService()
        self.register(pexels)
        dashscope = DashScopeImageGenService()
        self.register(dashscope)

    def register(self, service: ImageSearchService) -> None:
        """Register an image search service."""
        self._services[service.get_method().upper()] = service

    def get_available_methods(self) -> List[str]:
        """Return the list of registered method identifiers."""
        return list(self._services.keys())

    def get_service(self, method: str) -> Optional[ImageSearchService]:
        """Return the service registered for *method*, or ``None``."""
        return self._services.get(method.upper())

    async def execute(self, method: str, keywords: str, **kwargs) -> Optional[str]:
        """Execute an image search using the service identified by *method*.

        Falls back to the Pexels service if the requested method is not
        registered.
        """
        service = self.get_service(method)
        if service is None:
            # Fall back to Pexels
            service = self.get_service("PEXELS")
        if service is None:
            return ImageSearchService.get_fallback_image(keywords)

        return await service.search_image(keywords, **kwargs)
