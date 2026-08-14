"""OpenAI 兼容图片中转站提供商。

本适配器只负责协议转换、响应规范化和生成字节的短期持久化。中转站当前把
Base64 数据 URI 放在 ``data[*].url`` 中，因此必须先归档到 MinIO，再把稳定
素材 URL 交给文章和微信发布链路，禁止将超长 Base64 写入数据库正文。
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.config import settings as application_settings
from app.services.image_generation_models import (
    GeneratedImage,
    ImageErrorCategory,
    ImageGenerationRequest,
    ImageProviderError,
)
from app.services.storage_service import generate_object_key, storage_service


logger = logging.getLogger(__name__)
_DATA_URI_PATTERN = re.compile(
    r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$",
    re.DOTALL,
)
_CONTENT_TYPE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}
_TEMPORARY_STATUS_CODES = {408, 409, 425}


class OpenAICompatibleImageProvider:
    """通过 OpenAI 图片协议调用中转站并返回统一图片结果。"""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        settings: Any = application_settings,
        storage=storage_service,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        edit_model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """注入站点级配置、存储与客户端，使同一协议可安全复用到多个站点。"""
        self.settings = settings
        if name:
            self.name = name
        self.storage = storage
        self.client_factory = client_factory
        self.base_url = str(
            base_url if base_url is not None else settings.image_generation_base_url
        ).strip().rstrip("/")
        self.api_key = str(
            api_key if api_key is not None else settings.image_generation_api_key
        ).strip()
        self.model = str(
            model if model is not None else settings.image_generation_model
        ).strip()
        self.edit_model = str(
            edit_model if edit_model is not None else (
                settings.image_generation_edit_model or self.model
            )
        ).strip()
        # 同一适配器可承担主、备两层中转站；超时由装配层传入而非依赖全局设置，
        # 这样备用站不会继承主站过长的历史 30 分钟超时。
        self.timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else settings.image_generation_timeout_seconds
        )
        self.max_response_bytes = int(settings.image_generation_max_response_bytes)

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """根据是否包含参考图选择文本生图或 multipart 图片编辑接口。"""
        self._validate_configuration()
        normalized_size = _normalize_openai_image_size(request.size)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client_options = {
            "timeout": self.timeout_seconds,
            "follow_redirects": True,
            "http2": False,
        }

        try:
            async with self.client_factory(**client_options) as client:
                if request.reference_image_bytes:
                    response = await client.post(
                        f"{self.base_url}/images/edits",
                        headers=headers,
                        data={
                            "model": self.edit_model,
                            "prompt": request.prompt,
                            "size": normalized_size,
                            "n": str(request.n),
                            "response_format": "url",
                        },
                        files={
                            "image": (
                                _reference_filename(request.reference_content_type or "image/png"),
                                request.reference_image_bytes,
                                request.reference_content_type,
                            )
                        },
                    )
                    model = self.edit_model
                    operation = "edit"
                else:
                    response = await client.post(
                        f"{self.base_url}/images/generations",
                        headers={**headers, "Content-Type": "application/json"},
                        json={
                            "model": self.model,
                            "prompt": request.prompt,
                            "size": normalized_size,
                            "n": request.n,
                            "response_format": "url",
                        },
                    )
                    model = self.model
                    operation = "generation"
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ImageProviderError(
                f"图片中转站网络故障：{type(exc).__name__}",
                category=ImageErrorCategory.TEMPORARY,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                f"图片中转站传输失败：{type(exc).__name__}",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc

        self._raise_for_error_response(response)
        logger.info(
            "图片中转站响应成功 operation=%s model=%s requested_size=%s mapped_size=%s",
            operation,
            model,
            request.size,
            normalized_size,
        )
        return self._normalize_result(response, request.tenant_id, model)

    def _validate_configuration(self) -> None:
        """在发起付费请求前一次性校验主提供商配置。"""
        missing = []
        if not self.base_url:
            missing.append("IMAGE_GENERATION_BASE_URL")
        if not self.api_key:
            missing.append("IMAGE_GENERATION_API_KEY")
        if not self.model:
            missing.append("IMAGE_GENERATION_MODEL")
        if missing:
            raise ImageProviderError(
                f"图片中转站缺少配置：{', '.join(missing)}",
                category=ImageErrorCategory.CONFIGURATION,
                provider=self.name,
            )

    def _raise_for_error_response(self, response: Any) -> None:
        """把 HTTP 状态转换为稳定错误类别，响应正文仅保留短摘要。"""
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
        elif status_code in _TEMPORARY_STATUS_CODES or status_code >= 500:
            category = (
                ImageErrorCategory.UPSTREAM
                if status_code >= 500
                else ImageErrorCategory.TEMPORARY
            )
        else:
            category = ImageErrorCategory.INVALID_REQUEST
        raise ImageProviderError(
            f"图片中转站返回 HTTP {status_code}：{message}",
            category=category,
            provider=self.name,
            status_code=status_code,
        )

    def _normalize_result(
        self,
        response: Any,
        tenant_id: int,
        model: str,
    ) -> GeneratedImage:
        """兼容 URL、数据 URI 和 b64_json，并将字节结果写入 MinIO。"""
        try:
            payload = response.json()
        except Exception as exc:
            raise ImageProviderError(
                "图片中转站响应不是有效 JSON",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        first = data[0] if isinstance(data, list) and data else None
        if not isinstance(first, dict):
            raise ImageProviderError(
                "图片中转站未返回有效图片结果",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )

        result_url = str(first.get("url") or "").strip()
        if result_url:
            parsed = urlsplit(result_url)
            if parsed.scheme.lower() == "https" and parsed.netloc:
                return GeneratedImage(url=result_url, provider=self.name, model=model)
            if result_url.startswith("data:"):
                image_bytes, content_type = self._decode_data_uri(result_url)
                return self._archive_bytes(image_bytes, content_type, tenant_id, model)

        encoded_image = str(first.get("b64_json") or "").strip()
        if encoded_image:
            image_bytes = self._decode_base64(encoded_image)
            return self._archive_bytes(image_bytes, "image/png", tenant_id, model)

        raise ImageProviderError(
            "图片中转站结果不包含 HTTPS URL 或 Base64 图片",
            category=ImageErrorCategory.EMPTY_RESULT,
            provider=self.name,
        )

    def _decode_data_uri(self, data_uri: str) -> tuple[bytes, str]:
        """严格解析图片数据 URI，并拒绝非图片或过大的上游响应。"""
        match = _DATA_URI_PATTERN.match(data_uri)
        if not match:
            raise ImageProviderError(
                "图片中转站返回了不支持的数据 URI",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            )
        content_type = match.group(1).lower()
        if content_type not in _CONTENT_TYPE_EXTENSIONS:
            raise ImageProviderError(
                f"图片中转站返回了不支持的图片格式：{content_type}",
                category=ImageErrorCategory.INVALID_REQUEST,
                provider=self.name,
            )
        return self._decode_base64(match.group(2)), content_type

    def _decode_base64(self, encoded_image: str) -> bytes:
        """解码 Base64 并以解码后字节数实施内存上限。"""
        try:
            image_bytes = base64.b64decode(encoded_image, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageProviderError(
                "图片中转站 Base64 响应不完整",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc
        if not image_bytes:
            raise ImageProviderError(
                "图片中转站返回了空图片",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        if len(image_bytes) > self.max_response_bytes:
            raise ImageProviderError(
                "图片中转站返回图片超过允许大小",
                category=ImageErrorCategory.INVALID_REQUEST,
                provider=self.name,
            )
        return image_bytes

    def _archive_bytes(
        self,
        image_bytes: bytes,
        content_type: str,
        tenant_id: int,
        model: str,
    ) -> GeneratedImage:
        """把生成字节写入 MinIO，并把存储失败标记为不可降级错误。"""
        extension = _CONTENT_TYPE_EXTENSIONS[content_type]
        object_key = generate_object_key(
            tenant_id,
            f"generated.{extension}",
            prefix="generated-images",
        )
        try:
            self.storage.upload_bytes(object_key, image_bytes, content_type)
            image_url = self.storage.get_url(object_key)
        except Exception as exc:
            raise ImageProviderError(
                f"生成图片归档失败：{type(exc).__name__}",
                category=ImageErrorCategory.STORAGE,
                provider=self.name,
            ) from exc
        return GeneratedImage(url=image_url, provider=self.name, model=model)


def _normalize_openai_image_size(size: str) -> str:
    """把业务宽高映射为 OpenAI 图片模型支持的三个稳定规格。"""
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


def _reference_filename(content_type: str) -> str:
    """根据白名单 MIME 类型生成 multipart 文件名，未知类型使用 PNG 后缀。"""
    extension = _CONTENT_TYPE_EXTENSIONS.get(content_type.lower(), "png")
    return f"reference.{extension}"
