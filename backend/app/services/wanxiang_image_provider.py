"""通义万相图片提供商适配器。

该模块是业务层访问旧 ``WanxiangImageService`` 的唯一边界。统一请求中的参考图
公网 URL 继续交给万相图像编辑接口；参考图字节由主提供商使用，不在这里重复
上传，避免适配器获得 COS 临时对象的生命周期职责。
"""

from __future__ import annotations

from app.services.image_generation_models import (
    GeneratedImage,
    ImageErrorCategory,
    ImageGenerationRequest,
    ImageProviderError,
)
from app.services.wanxiang_service import (
    WANXIANG_IMAGE_TO_IMAGE_MODEL,
    WANXIANG_MODEL,
    WanxiangImageService,
)


class WanxiangImageProvider:
    """把统一图片请求转换为现有万相异步任务调用。"""

    name = "wanxiang"

    def __init__(self, service: WanxiangImageService | None = None) -> None:
        """允许测试注入万相替身，生产环境默认创建现有服务。"""
        self.service = service or WanxiangImageService()

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """调用万相并把空结果或异常转换为统一上游错误。"""
        if request.reference_image_bytes and not request.reference_image_url:
            raise ImageProviderError(
                "万相降级缺少可访问的参考图 URL",
                category=ImageErrorCategory.INVALID_REQUEST,
                provider=self.name,
            )
        normalized_size = str(request.size or "1024*1024").replace("x", "*")
        try:
            image_url = await self.service.generate_image(
                request.prompt,
                size=normalized_size,
                n=request.n,
                no_text=request.no_text,
                reference_image_url=request.reference_image_url,
            )
        except ImageProviderError:
            raise
        except Exception as exc:
            raise ImageProviderError(
                f"万相调用异常：{type(exc).__name__}",
                category=ImageErrorCategory.UPSTREAM,
                provider=self.name,
            ) from exc
        if not image_url:
            raise ImageProviderError(
                "万相未返回有效图片地址",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        model = (
            WANXIANG_IMAGE_TO_IMAGE_MODEL
            if request.reference_image_url
            else WANXIANG_MODEL
        )
        return GeneratedImage(
            url=image_url,
            provider=self.name,
            model=model,
        )
