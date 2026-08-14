"""图片生成主备路由与兼容入口。

所有业务流程只依赖本服务。主提供商失败时，只有经过领域层标记为临时性或
上游可用性故障的异常才允许调用备用提供商；鉴权、参数、配置和本地存储错误
直接暴露，避免系统长期运行在意外降级状态。
"""

from __future__ import annotations

import logging
from typing import Mapping

from app.config import settings as application_settings
from app.services.image_generation_models import (
    GeneratedImage,
    ImageErrorCategory,
    ImageGenerationProvider,
    ImageGenerationRequest,
    ImageProviderError,
)
from app.services.image_provider_health_service import ImageProviderHealthService


logger = logging.getLogger(__name__)


def is_image_generation_configured(settings=application_settings) -> bool:
    """判断统一图片路由是否至少具备一个可用提供商的凭证。

    业务入口不应继续把“可以生成图片”等同于“配置了百炼密钥”。主提供商使用
    OpenAI 兼容中转站时只需要独立图片密钥；保留百炼密钥判断则是为了兼容万相
    作为主提供商或备用提供商的历史部署。
    """
    return bool(
        str(getattr(settings, "image_generation_api_key", "") or "").strip()
        or str(getattr(settings, "image_generation_ark_api_key", "") or "").strip()
        or str(getattr(settings, "dashscope_api_key", "") or "").strip()
    )


class ImageGenerationFallbackError(RuntimeError):
    """全部图片提供商失败时携带各层脱敏摘要的最终异常。"""

    def __init__(self, *errors: ImageProviderError):
        """只拼接错误分类与短摘要，不包含请求头、Base64 或完整响应正文。"""
        summaries = []
        for error in errors:
            summary = " ".join(str(error).split())[:300]
            summaries.append(
                f"{error.provider} 失败[{error.category.value}]：{summary}"
            )
        super().__init__("图片提供商全部失败：" + "；".join(summaries))
        self.errors = tuple(errors)
        self.primary_error = errors[0] if errors else None
        self.fallback_error = errors[-1] if len(errors) > 1 else None


class ImageGenerationService:
    """按配置的有序链调用图片提供商，成功后立即停止后续计费。"""

    def __init__(
        self,
        *,
        settings=application_settings,
        providers: Mapping[str, ImageGenerationProvider] | None = None,
        health_service: ImageProviderHealthService | None = None,
    ) -> None:
        """延迟创建默认提供商，同时支持测试注入确定性替身。"""
        self.settings = settings
        self.primary_name = str(settings.image_generation_provider or "").strip().lower()
        self.fallback_name = str(
            settings.image_generation_fallback_provider or ""
        ).strip().lower()
        raw_chain = str(
            getattr(settings, "image_generation_provider_chain", "") or ""
        ).strip()
        legacy_chain = [self.primary_name, self.fallback_name]
        self.provider_names = tuple(dict.fromkeys(
            name.strip().lower()
            for name in (raw_chain.split(",") if raw_chain else legacy_chain)
            if name and name.strip()
        ))
        self.providers = dict(providers or _build_default_providers(settings))
        self.health_service = health_service or ImageProviderHealthService(
            failure_threshold=getattr(
                settings,
                "image_provider_circuit_failure_threshold",
                3,
            ),
            cooldown_seconds=getattr(
                settings, "image_provider_circuit_cooldown_seconds", 600
            ),
        )

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        """逐级调用提供商；不可降级错误立即停止，避免掩盖错误配置。"""
        errors: list[ImageProviderError] = []
        operation = (
            "edit"
            if request.reference_image_bytes or request.reference_image_url
            else "generation"
        )
        for provider_index, provider_name in enumerate(self.provider_names):
            provider = self._require_provider(
                provider_name,
                role="主" if provider_index == 0 else f"第 {provider_index + 1} 层",
            )
            if not self.health_service.allow_request(provider_name, operation):
                skipped_error = ImageProviderError(
                    "图片提供商处于故障冷却期，已跳过本次请求",
                    category=ImageErrorCategory.TEMPORARY,
                    provider=provider_name,
                )
                errors.append(skipped_error)
                logger.warning(
                    "图片提供商熔断中，跳过 provider=%s operation=%s",
                    provider_name,
                    operation,
                )
                continue
            try:
                result = await provider.generate(request)
                self.health_service.record_success(provider_name, operation)
                from app.services.model_usage_service import record_image_generation_usage

                record_image_generation_usage(
                    result.provider,
                    result.model,
                    request.size,
                    has_reference_image=bool(
                        request.reference_image_bytes or request.reference_image_url
                    ),
                )
                if provider_index > 0:
                    logger.warning(
                        "图片降级成功 provider=%s model=%s level=%d",
                        result.provider,
                        result.model,
                        provider_index + 1,
                    )
                    return result.mark_fallback_used()
                return result
            except ImageProviderError as provider_error:
                errors.append(provider_error)
                self.health_service.record_failure(
                    provider_name,
                    operation,
                    provider_error.category,
                )
                if not provider_error.can_fallback:
                    logger.error(
                        "图片提供商失败且禁止降级 provider=%s category=%s",
                        provider_error.provider,
                        provider_error.category.value,
                    )
                    raise
                logger.error(
                    "图片提供商失败，准备下一层 provider=%s category=%s level=%d",
                    provider_error.provider,
                    provider_error.category.value,
                    provider_index + 1,
                )
        if errors:
            raise ImageGenerationFallbackError(*errors) from errors[-1]
        raise ImageProviderError(
            "图片提供商链为空",
            category=ImageErrorCategory.CONFIGURATION,
            provider="unknown",
        )

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024*1024",
        n: int = 1,
        no_text: bool = True,
        reference_image_url: str | None = None,
        reference_image_bytes: bytes | None = None,
        reference_content_type: str | None = None,
        tenant_id: int = 0,
    ) -> str:
        """为迁移旧调用点提供 URL 兼容接口，内部仍走统一领域请求。"""
        result = await self.generate(ImageGenerationRequest(
            prompt=prompt,
            size=size,
            n=n,
            no_text=no_text,
            tenant_id=tenant_id,
            reference_image_url=reference_image_url,
            reference_image_bytes=reference_image_bytes,
            reference_content_type=reference_content_type,
        ))
        return result.url

    def _require_provider(self, name: str, *, role: str) -> ImageGenerationProvider:
        """在发起外部请求前报告未知或缺失的提供商配置。"""
        provider = self.providers.get(name)
        if provider is None:
            raise ImageProviderError(
                f"未配置可用的图片{role}提供商：{name or '空值'}",
                category=ImageErrorCategory.CONFIGURATION,
                provider=name or "unknown",
            )
        return provider


def _build_default_providers(settings) -> dict[str, ImageGenerationProvider]:
    """集中组装生产提供商，避免业务模块自行实例化供应商客户端。"""
    from app.services.openai_compatible_image_provider import OpenAICompatibleImageProvider
    from app.services.volcengine_ark_image_provider import VolcengineArkImageProvider
    from app.services.wanxiang_image_provider import WanxiangImageProvider

    kuai_provider = OpenAICompatibleImageProvider(
        settings=settings,
        name="kuai_openai_compatible",
        timeout_seconds=getattr(
            settings,
            "image_generation_primary_timeout_seconds",
            settings.image_generation_timeout_seconds,
        ),
    )
    openai_provider = OpenAICompatibleImageProvider(
        settings=settings,
        name="openai_compatible",
        base_url=getattr(settings, "image_generation_secondary_base_url", "") or (
            settings.image_generation_base_url
        ),
        api_key=getattr(settings, "image_generation_secondary_api_key", "") or (
            settings.image_generation_api_key
        ),
        model=getattr(settings, "image_generation_secondary_model", "") or (
            settings.image_generation_model
        ),
        edit_model=getattr(settings, "image_generation_secondary_edit_model", "") or (
            settings.image_generation_edit_model
        ),
        timeout_seconds=getattr(
            settings,
            "image_generation_secondary_timeout_seconds",
            settings.image_generation_timeout_seconds,
        ),
    )
    wanxiang_provider = WanxiangImageProvider()
    ark_provider = VolcengineArkImageProvider(
        settings=settings,
        timeout_seconds=getattr(
            settings,
            "image_generation_ark_timeout_seconds",
            settings.image_generation_timeout_seconds,
        ),
    )
    return {
        kuai_provider.name: kuai_provider,
        openai_provider.name: openai_provider,
        ark_provider.name: ark_provider,
        wanxiang_provider.name: wanxiang_provider,
    }


# 单例只保存无状态的客户端配置；Worker 修改环境后必须重启才能重新加载。
image_generation_service = ImageGenerationService()
