"""图片生成提供商共享的领域协议与错误分类。

业务编排层只能依赖本模块中的稳定请求和结果对象，不能感知 OpenAI 兼容接口、
万相异步任务等供应商细节。错误是否允许降级也在这里统一定义，防止不同入口
对鉴权失败、限流和网络故障作出互相矛盾的处理。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol


class ImageErrorCategory(str, Enum):
    """图片提供商错误类别，用于决定是否允许主备切换。"""

    TEMPORARY = "temporary"
    RATE_LIMIT = "rate_limit"
    UPSTREAM = "upstream"
    EMPTY_RESULT = "empty_result"
    TRUNCATED_RESPONSE = "truncated_response"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    INVALID_REQUEST = "invalid_request"
    STORAGE = "storage"


_FALLBACK_ELIGIBLE_CATEGORIES = frozenset({
    ImageErrorCategory.TEMPORARY,
    ImageErrorCategory.RATE_LIMIT,
    ImageErrorCategory.UPSTREAM,
    ImageErrorCategory.EMPTY_RESULT,
    ImageErrorCategory.TRUNCATED_RESPONSE,
})

_TEMPORARY_HTTP_STATUS_CODES = frozenset({408, 409, 425})
_AUTHENTICATION_ERROR_MARKERS = (
    "invalid key",
    "invalid api key",
    "invalid api_key",
    "invalid token",
    "api key not found",
    "api_key not found",
    "unauthorized",
    "authentication failed",
    "permission denied",
    "insufficient permission",
    "access denied",
)
_RATE_LIMIT_ERROR_MARKERS = (
    "rate limit",
    "rate-limit",
    "too many requests",
    "quota exceeded",
    "concurrency limit",
)
_TEMPORARY_ERROR_MARKERS = (
    "temporarily unavailable",
    "temporary unavailable",
    "service unavailable",
    "service busy",
    "server busy",
    "overloaded",
    "try again",
    "upstream unavailable",
    "gateway timeout",
)


def classify_http_error_category(status_code: int, message: str) -> ImageErrorCategory:
    """将图片 Provider 的 HTTP 错误归类为鉴权、限流或可用性故障。

    设计原因：部分中转站会用 403 表示临时风控、容量不足或服务不可用，不能
    仅凭状态码阻断备用链路；但明确的密钥和权限错误必须保留为不可降级错误，
    否则会掩盖配置问题并让每张图片重复消耗备用模型额度。对未识别的 403
    采取保守的鉴权分类，只有明确带有临时故障语义时才允许降级。
    """
    normalized_status = int(status_code)
    normalized_message = " ".join(str(message or "").lower().split())

    if normalized_status == 401:
        return ImageErrorCategory.AUTHENTICATION
    if normalized_status == 403:
        if any(marker in normalized_message for marker in _AUTHENTICATION_ERROR_MARKERS):
            return ImageErrorCategory.AUTHENTICATION
        if any(marker in normalized_message for marker in _RATE_LIMIT_ERROR_MARKERS):
            return ImageErrorCategory.RATE_LIMIT
        if any(marker in normalized_message for marker in _TEMPORARY_ERROR_MARKERS):
            return ImageErrorCategory.TEMPORARY
        return ImageErrorCategory.AUTHENTICATION
    if normalized_status == 429:
        return ImageErrorCategory.RATE_LIMIT
    if normalized_status in _TEMPORARY_HTTP_STATUS_CODES:
        return ImageErrorCategory.TEMPORARY
    if normalized_status >= 500:
        return ImageErrorCategory.UPSTREAM
    return ImageErrorCategory.INVALID_REQUEST


class ImageProviderError(RuntimeError):
    """携带提供商和错误类别的可诊断异常。

    异常消息只能保存经过截断和脱敏的摘要，不能包含 API 密钥、完整 Base64 或
    完整签名 URL。统一路由通过 ``can_fallback`` 判断是否允许调用备用提供商。
    """

    def __init__(
        self,
        message: str,
        category: ImageErrorCategory,
        provider: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.status_code = status_code

    @property
    def can_fallback(self) -> bool:
        """仅临时性或上游可用性故障允许切换备用提供商。"""
        return self.category in _FALLBACK_ELIGIBLE_CATEGORIES


@dataclass(frozen=True)
class ImageGenerationRequest:
    """提供商无关的单张图片生成请求。

    ``reference_image_bytes`` 供支持 multipart 的主提供商直接上传；
    ``reference_image_url`` 供万相降级使用。两者可同时存在，但参考图字节存在时
    必须提供可信 MIME 类型，避免请求端自行猜测格式。
    """

    prompt: str
    size: str = "1024*1024"
    n: int = 1
    no_text: bool = True
    tenant_id: int = 0
    reference_image_bytes: bytes | None = None
    reference_content_type: str | None = None
    reference_image_url: str | None = None

    def __post_init__(self) -> None:
        normalized_prompt = str(self.prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("图片生成提示词不能为空")
        if not 1 <= int(self.n) <= 4:
            raise ValueError("图片生成数量必须在 1 到 4 之间")
        if self.reference_image_bytes and not str(self.reference_content_type or "").strip():
            raise ValueError("参考图字节必须提供 MIME 类型")
        object.__setattr__(self, "prompt", normalized_prompt)


@dataclass(frozen=True)
class GeneratedImage:
    """统一图片结果，URL 必须能被后续文章和归档链路消费。"""

    url: str
    provider: str
    model: str
    fallback_used: bool = False

    def mark_fallback_used(self) -> "GeneratedImage":
        """返回标记为降级结果的新对象，保持结果对象不可变。"""
        return replace(self, fallback_used=True)


class ImageGenerationProvider(Protocol):
    """所有图片提供商必须实现的最小异步接口。"""

    name: str

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """生成一张图片，失败时抛出分类后的 ``ImageProviderError``。"""
        ...
