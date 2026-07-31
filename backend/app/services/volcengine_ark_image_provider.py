"""火山方舟 Seedream 图片生成适配器。

本模块只处理方舟 ``images/generations`` 协议，不参与文章、定时任务或存储
编排。方舟支持将参考图以 Data URI 直接传入，因此 ERP 本地素材能够在不依赖
公网 URL 的情况下作为图生图输入；生成结果统一归档后再交由发布链路使用。
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Callable

import httpx

from app.config import settings as application_settings
from app.services.image_generation_models import (
    GeneratedImage,
    ImageErrorCategory,
    ImageGenerationRequest,
    ImageProviderError,
)
from app.services.storage_service import generate_object_key, storage_service


_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_TEMPORARY_STATUS_CODES = {408, 409, 425}


class VolcengineArkImageProvider:
    """将统一图片请求转换为火山方舟 Seedream 请求。

    该提供商作为万相的替代兜底。它不要求参考图提供公网可访问 URL，从而使主
    链路可以始终以本地 MinIO 中的 ERP 原图字节为准，避免因临时公网中转失效而
    导致图生图失败。
    """

    name = "volcengine_ark"

    def __init__(
        self,
        *,
        settings: Any = application_settings,
        storage=storage_service,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        object_key_factory: Callable[[int], str] | None = None,
    ) -> None:
        """注入基础设施依赖，便于在不访问真实方舟的情况下测试协议。"""
        self.settings = settings
        self.storage = storage
        self.client_factory = client_factory
        self.object_key_factory = object_key_factory or self._default_object_key
        self.base_url = str(
            getattr(settings, "image_generation_ark_base_url", "") or ""
        ).strip().rstrip("/")
        self.api_key = str(
            getattr(settings, "image_generation_ark_api_key", "") or ""
        ).strip()
        self.model = str(
            getattr(settings, "image_generation_ark_model", "") or ""
        ).strip()
        self.timeout_seconds = int(
            getattr(settings, "image_generation_timeout_seconds", 240)
        )
        self.max_response_bytes = int(
            getattr(settings, "image_generation_max_response_bytes", 20 * 1024 * 1024)
        )

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """调用 Seedream，并将可过期的响应内容归档为稳定素材地址。"""
        self._validate_configuration()
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "size": _normalize_ark_size(request.size),
            "response_format": "b64_json",
            # 文章发布不应出现模型水印；若账号策略不允许，上游会返回可诊断错误。
            "watermark": False,
        }
        reference_image = self._build_reference_image(request)
        if reference_image:
            payload["image"] = reference_image

        try:
            async with self.client_factory(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                http2=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/images/generations",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ImageProviderError(
                f"火山方舟网络故障：{type(exc).__name__}",
                category=ImageErrorCategory.TEMPORARY,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                f"火山方舟传输失败：{type(exc).__name__}",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc

        self._raise_for_error_response(response)
        image_bytes, content_type = self._extract_image_bytes(response)
        object_key = self.object_key_factory(request.tenant_id)
        try:
            self.storage.upload_bytes(object_key, image_bytes, content_type)
            image_url = self.storage.get_url(object_key)
        except Exception as exc:
            raise ImageProviderError(
                f"火山方舟生成图归档失败：{type(exc).__name__}",
                category=ImageErrorCategory.STORAGE,
                provider=self.name,
            ) from exc
        return GeneratedImage(url=image_url, provider=self.name, model=self.model)

    def _validate_configuration(self) -> None:
        """在产生付费请求前报告缺失配置，但绝不回显密钥值。"""
        missing = []
        if not self.base_url:
            missing.append("IMAGE_GENERATION_ARK_BASE_URL")
        if not self.api_key:
            missing.append("IMAGE_GENERATION_ARK_API_KEY")
        if not self.model:
            missing.append("IMAGE_GENERATION_ARK_MODEL")
        if missing:
            raise ImageProviderError(
                f"火山方舟图片提供商缺少配置：{', '.join(missing)}",
                category=ImageErrorCategory.CONFIGURATION,
                provider=self.name,
            )

    def _build_reference_image(self, request: ImageGenerationRequest) -> str:
        """优先把本地图片字节编码为 Data URI，URL 仅用于旧入口兼容。"""
        if request.reference_image_bytes:
            content_type = str(request.reference_content_type or "image/jpeg").lower()
            if content_type not in _CONTENT_TYPE_EXTENSIONS:
                raise ImageProviderError(
                    f"火山方舟不支持参考图格式：{content_type}",
                    category=ImageErrorCategory.INVALID_REQUEST,
                    provider=self.name,
                )
            encoded = base64.b64encode(request.reference_image_bytes).decode("ascii")
            return f"data:{content_type};base64,{encoded}"
        return str(request.reference_image_url or "").strip()

    def _raise_for_error_response(self, response: Any) -> None:
        """把方舟 HTTP 错误映射为统一的可降级错误分类。"""
        status_code = int(response.status_code)
        if 200 <= status_code < 300:
            return

        message = "上游未返回错误说明"
        try:
            payload = response.json()
            message = str((payload.get("error") or {}).get("message") or message)
        except Exception:
            pass
        message = " ".join(message.split())[:300]
        if status_code in {401, 403}:
            category = ImageErrorCategory.AUTHENTICATION
        elif status_code == 429:
            category = ImageErrorCategory.RATE_LIMIT
        elif status_code in _TEMPORARY_STATUS_CODES:
            category = ImageErrorCategory.TEMPORARY
        elif status_code >= 500:
            category = ImageErrorCategory.UPSTREAM
        else:
            category = ImageErrorCategory.INVALID_REQUEST
        raise ImageProviderError(
            f"火山方舟返回 HTTP {status_code}：{message}",
            category=category,
            provider=self.name,
            status_code=status_code,
        )

    def _extract_image_bytes(self, response: Any) -> tuple[bytes, str]:
        """严格解析 Base64 输出，并在归档前实施字节上限。"""
        try:
            payload = response.json()
        except Exception as exc:
            raise ImageProviderError(
                "火山方舟响应不是有效 JSON",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        first = data[0] if isinstance(data, list) and data else None
        encoded = str(first.get("b64_json") or "").strip() if isinstance(first, dict) else ""
        if not encoded:
            raise ImageProviderError(
                "火山方舟未返回 Base64 图片",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageProviderError(
                "火山方舟返回的 Base64 图片不完整",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc
        if not image_bytes:
            raise ImageProviderError(
                "火山方舟返回空图片",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        if len(image_bytes) > self.max_response_bytes:
            raise ImageProviderError(
                "火山方舟图片超过允许大小",
                category=ImageErrorCategory.INVALID_REQUEST,
                provider=self.name,
            )
        return image_bytes, "image/png"

    @staticmethod
    def _default_object_key(tenant_id: int) -> str:
        """为方舟结果生成与其他生图一致的本地素材键。"""
        return generate_object_key(tenant_id, "ark-generated.png", prefix="generated-images")


def _normalize_ark_size(size: str) -> str:
    """把旧业务的星号分隔规格转换为方舟认可的 ``宽x高`` 格式。"""
    normalized = str(size or "1024*1024").lower().replace("*", "x")
    try:
        width_text, height_text = normalized.split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError):
        return "1024x1024"
    if height > width * 1.1:
        return "1024x1536"
    if width > height * 1.1:
        return "1536x1024"
    return "1024x1024"
