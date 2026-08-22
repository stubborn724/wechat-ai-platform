"""九野星霸 GPT Image 2 异步图片生成适配器。

九野星霸接口与 OpenAI 同步图片接口不同：提交请求只返回任务 ID，调用方必须轮询
任务状态并下载最终图片。本模块封装完整生命周期，使文章编排层仍只依赖统一的
``ImageGenerationProvider`` 协议，不感知异步任务、轮询或临时结果地址。
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.config import settings as application_settings
from app.services.image_generation_models import (
    GeneratedImage,
    ImageErrorCategory,
    ImageGenerationRequest,
    ImageProviderError,
    classify_http_error_category,
)
from app.services.storage_service import generate_object_key, storage_service


_SUPPORTED_REFERENCE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_SUCCESS_STATUSES = {"succeeded", "success", "completed", "done"}
_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}


class JiuyeImageProvider:
    """将统一图片请求转换为星霸提交、轮询、下载和归档流程。"""

    name = "jiuye_image_2"

    def __init__(
        self,
        *,
        settings: Any = application_settings,
        storage=storage_service,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        object_key_factory: Callable[[int], str] | None = None,
    ) -> None:
        """注入配置与基础设施，保证异步协议可以用纯替身进行单元测试。"""
        self.storage = storage
        self.client_factory = client_factory
        self.object_key_factory = object_key_factory or self._default_object_key
        self.base_url = str(
            getattr(settings, "image_generation_jiuye_base_url", "") or ""
        ).strip().rstrip("/")
        self.submit_path = _normalize_path(
            getattr(settings, "image_generation_jiuye_submit_path", "/v1/xingba/image"),
            "/v1/xingba/image",
        )
        self.poll_path = _normalize_path(
            getattr(settings, "image_generation_jiuye_poll_path", "/v1/xingba/image/{task_id}"),
            "/v1/xingba/image/{task_id}",
        )
        self.api_key = str(
            getattr(settings, "image_generation_jiuye_api_key", "") or ""
        ).strip()
        self.model = str(
            getattr(settings, "image_generation_jiuye_model", "gpt-image-2") or "gpt-image-2"
        ).strip()
        self.timeout_seconds = int(
            getattr(settings, "image_generation_jiuye_timeout_seconds", 240)
        )
        self.poll_interval_seconds = max(
            0.0,
            float(getattr(settings, "image_generation_jiuye_poll_interval_seconds", 3)),
        )
        self.max_response_bytes = int(
            getattr(settings, "image_generation_max_response_bytes", 20 * 1024 * 1024)
        )

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """提交九野任务并等待终态，成功后将临时结果归档到项目对象存储。"""
        self._validate_configuration()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "aspectRatio": _normalize_aspect_ratio(request.size),
            "imageSize": _normalize_image_size(request.size),
            "images": self._build_reference_images(request),
        }
        deadline = time.monotonic() + self.timeout_seconds

        try:
            async with self.client_factory(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                http2=False,
            ) as client:
                submit_response = await client.post(
                    f"{self.base_url}{self.submit_path}",
                    headers=headers,
                    json=payload,
                )
                self._raise_for_error_response(submit_response, "提交")
                task_id = self._extract_task_id(submit_response)
                result_url = await self._poll_result(
                    client=client,
                    headers=headers,
                    task_id=task_id,
                    deadline=deadline,
                )
                image_response = await client.get(result_url)
                self._raise_for_error_response(image_response, "结果下载")
        except ImageProviderError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ImageProviderError(
                f"九野映像网络故障：{type(exc).__name__}",
                category=ImageErrorCategory.TEMPORARY,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                f"九野映像传输失败：{type(exc).__name__}",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc

        image_bytes = bytes(image_response.content or b"")
        if not image_bytes:
            raise ImageProviderError(
                "九野映像返回空图片",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        if len(image_bytes) > self.max_response_bytes:
            raise ImageProviderError(
                "九野映像图片超过允许大小",
                category=ImageErrorCategory.INVALID_REQUEST,
                provider=self.name,
            )
        content_type = _normalize_result_content_type(
            image_response.headers.get("content-type") or ""
        )
        object_key = self.object_key_factory(request.tenant_id)
        try:
            self.storage.upload_bytes(object_key, image_bytes, content_type)
            archived_url = self.storage.get_url(object_key)
        except Exception as exc:
            raise ImageProviderError(
                f"九野映像生成图归档失败：{type(exc).__name__}",
                category=ImageErrorCategory.STORAGE,
                provider=self.name,
            ) from exc
        return GeneratedImage(
            url=archived_url,
            provider=self.name,
            model=self.model,
        )

    async def _poll_result(
        self,
        *,
        client: Any,
        headers: dict[str, str],
        task_id: str,
        deadline: float,
    ) -> str:
        """轮询已有任务直至成功、明确失败或超过提供商独立超时。"""
        poll_url = f"{self.base_url}{self.poll_path.format(task_id=task_id)}"
        while time.monotonic() < deadline:
            if self.poll_interval_seconds:
                await asyncio.sleep(self.poll_interval_seconds)
            response = await client.get(poll_url, headers=headers)
            self._raise_for_error_response(response, "轮询")
            payload = self._read_json(response, "轮询")
            status = str(payload.get("status") or "").strip().lower()
            if status in _SUCCESS_STATUSES:
                result_url = _extract_result_url(payload)
                if not _is_https_url(result_url):
                    raise ImageProviderError(
                        "九野星霸任务完成但未返回 HTTPS 图片地址",
                        category=ImageErrorCategory.EMPTY_RESULT,
                        provider=self.name,
                    )
                return result_url
            if status in _FAILURE_STATUSES:
                error_message = " ".join(
                    str(payload.get("error_msg") or "任务执行失败").split()
                )[:300]
                raise ImageProviderError(
                    f"九野星霸任务失败：{error_message}",
                    category=ImageErrorCategory.UPSTREAM,
                    provider=self.name,
                )
        raise ImageProviderError(
            "九野星霸任务轮询超时",
            category=ImageErrorCategory.TEMPORARY,
            provider=self.name,
        )

    def _build_reference_images(self, request: ImageGenerationRequest) -> list[str]:
        """优先使用本地参考图字节，兼容旧任务保留的 HTTPS 地址。"""
        if request.reference_image_bytes:
            content_type = str(request.reference_content_type or "").lower()
            if content_type not in _SUPPORTED_REFERENCE_TYPES:
                raise ImageProviderError(
                    f"九野映像不支持参考图格式：{content_type}",
                    category=ImageErrorCategory.INVALID_REQUEST,
                    provider=self.name,
                )
            encoded = base64.b64encode(request.reference_image_bytes).decode("ascii")
            return [f"data:{content_type};base64,{encoded}"]
        reference_url = str(request.reference_image_url or "").strip()
        return [reference_url] if _is_https_url(reference_url) else []

    def _validate_configuration(self) -> None:
        """在付费请求前校验必要配置，错误中不包含密钥值。"""
        missing = []
        if not self.base_url:
            missing.append("IMAGE_GENERATION_JIUYE_BASE_URL")
        if not self.api_key:
            missing.append("IMAGE_GENERATION_JIUYE_API_KEY")
        if not self.model:
            missing.append("IMAGE_GENERATION_JIUYE_MODEL")
        if missing:
            raise ImageProviderError(
                f"九野映像图片提供商缺少配置：{', '.join(missing)}",
                category=ImageErrorCategory.CONFIGURATION,
                provider=self.name,
            )

    def _raise_for_error_response(self, response: Any, operation: str) -> None:
        """把九野 HTTP 错误转换为统一的可降级错误。"""
        status_code = int(response.status_code)
        if 200 <= status_code < 300:
            return
        message = "上游未返回错误说明"
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            message = str(
                (error or {}).get("message")
                if isinstance(error, dict)
                else payload.get("message") or payload.get("error_msg") or message
            )
        except Exception:
            pass
        message = " ".join(message.split())[:300]
        raise ImageProviderError(
            f"九野映像{operation}返回 HTTP {status_code}：{message}",
            category=classify_http_error_category(status_code, message),
            provider=self.name,
            status_code=status_code,
        )

    def _extract_task_id(self, response: Any) -> str:
        """从提交响应提取稳定任务 ID，拒绝无法轮询的伪成功响应。"""
        payload = self._read_json(response, "提交")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        task_id = str(
            payload.get("task_id")
            or payload.get("id")
            or data.get("task_id")
            or data.get("id")
            or ""
        ).strip()
        if not task_id:
            raise ImageProviderError(
                "九野映像提交成功但未返回任务 ID",
                category=ImageErrorCategory.EMPTY_RESULT,
                provider=self.name,
            )
        return task_id

    def _read_json(self, response: Any, operation: str) -> dict[str, Any]:
        """严格读取 JSON，避免将网关错误页当作任务状态。"""
        try:
            payload = response.json()
        except Exception as exc:
            raise ImageProviderError(
                f"九野映像{operation}响应不是有效 JSON",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            ) from exc
        if not isinstance(payload, dict):
            raise ImageProviderError(
                f"九野映像{operation}响应格式错误",
                category=ImageErrorCategory.TRUNCATED_RESPONSE,
                provider=self.name,
            )
        return payload

    @staticmethod
    def _default_object_key(tenant_id: int) -> str:
        """生成与其他提供商一致的长期存储对象键。"""
        return generate_object_key(
            tenant_id,
            "jiuye-generated.png",
            prefix="generated-images",
        )


def _normalize_aspect_ratio(size: str) -> str:
    """把业务像素规格转换为星霸支持的宽高比。"""
    normalized = str(size or "1024*1024").lower().replace("*", "x")
    try:
        width_text, height_text = normalized.split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError):
        return "1:1"
    if height > width * 1.1:
        return "3:4"
    if width > height * 1.1:
        return "4:3"
    return "1:1"


def _normalize_image_size(size: str) -> str:
    """按最长边选择星霸的 1K、2K 或 4K 档位。"""
    normalized = str(size or "1024*1024").lower().replace("*", "x")
    try:
        longest = max(int(part) for part in normalized.split("x", maxsplit=1))
    except (TypeError, ValueError):
        return "1K"
    if longest >= 3072:
        return "4K"
    if longest >= 1536:
        return "2K"
    return "1K"


def _normalize_result_content_type(content_type: str) -> str:
    """只接受项目可归档的常见图片类型。"""
    normalized = str(content_type or "").split(";", maxsplit=1)[0].strip().lower()
    return normalized if normalized in _SUPPORTED_REFERENCE_TYPES else "image/png"


def _is_https_url(value: str) -> bool:
    """限制外部结果和参考地址为完整 HTTPS URL。"""
    parsed = urlsplit(str(value or "").strip())
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def _normalize_path(value: Any, default: str) -> str:
    """规范化可配置接口路径，避免环境变量缺少前导斜杠导致请求地址错误。"""
    path = str(value or default).strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def _extract_result_url(payload: dict[str, Any]) -> str:
    """兼容星霸常见的顶层、data 和 images 结果字段。"""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    candidates = [
        payload.get("result_url"),
        payload.get("url"),
        data.get("result_url"),
        data.get("url"),
    ]
    images = payload.get("images") or data.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        candidates.append(first.get("url") if isinstance(first, dict) else first)
    return next((str(candidate).strip() for candidate in candidates if candidate), "")
