"""Strategy-based image search services (adapted from ai-passage-creator).

Provides pluggable image search back-ends that can be registered with
:class:`ImageServiceStrategy` and dispatched by method name.
"""

import abc
import logging
from typing import List, Optional

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

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
        """Return the unique method identifier for this service (e.g. ``"DASHSCOPE"``)."""
        ...

    @staticmethod
    def get_fallback_image(keywords: str) -> str:
        """Return a deterministic picsum placeholder."""
        seed = keywords.replace(" ", "-") or "default"
        return f"https://picsum.photos/seed/{seed}/800/600"


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Strategy registry / dispatcher
# ---------------------------------------------------------------------------


class DashScopeImageGenService(ImageSearchService):
    """兼容历史 ``DASHSCOPE`` 来源值的统一 AI 图片生成入口。

    历史任务和前端仍使用 ``DASHSCOPE`` 作为“AI 生成”标识，但实际主备提供商
    由全局图片配置决定，业务代码不再绑定通义万相。
    """

    def __init__(self) -> None:
        from app.services.image_generation_service import image_generation_service
        self.generator = image_generation_service

    def get_method(self) -> str:
        return "DASHSCOPE"

    async def search_image(self, keywords: str, **kwargs) -> Optional[str]:
        prompt = kwargs.get("prompt") or keywords
        if not prompt:
            logger.error("Wanxiang generation skipped because the final prompt is empty")
            return None
        # 默认不生成文字，除非 prompt 中明确要求了
        url = await self.generator.generate_image(
            prompt,
            no_text=True,
            tenant_id=int(kwargs.get("tenant_id") or 0),
        )
        if url:
            return url
        # 仿写流程不得用随机图库图伪装成功，必须把失败交还给上游明确处理。
        logger.error("AI 图片生成失败；随机图库回退已阻止 keywords=%r", keywords[:120])
        return None


class ImageServiceStrategy:
    """Registry of image-search strategies dispatched by method name.

    Usage::

        strategy = ImageServiceStrategy()
        url = await strategy.execute("DASHSCOPE", "mountain sunset")
    """

    def __init__(self) -> None:
        self._services: dict = {}

        # Register built-in services
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

        Falls back to the DashScope service if the requested method is not
        registered.
        """
        service = self.get_service(method)
        if service is None:
            # Fall back to DashScope
            service = self.get_service("DASHSCOPE")
        if service is None:
            logger.error("No image provider is registered; 随机图库回退已阻止 method=%s", method)
            return None

        return await service.search_image(keywords, **kwargs)
