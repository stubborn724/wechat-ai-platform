"""图片生成主备路由测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """主备路由测试使用内存提供商，不访问业务数据库。"""
    yield


class FakeProvider:
    """可配置成功结果或异常的提供商替身。"""

    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


def build_settings():
    """构造主备提供商名称配置。"""
    return SimpleNamespace(
        image_generation_provider="openai_compatible",
        image_generation_fallback_provider="wanxiang",
        image_generation_provider_chain="",
    )


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback():
    """主模型成功时不得额外调用万相造成重复计费。"""
    from app.services.image_generation_models import GeneratedImage, ImageGenerationRequest
    from app.services.image_generation_service import ImageGenerationService

    primary = FakeProvider(
        "openai_compatible",
        result=GeneratedImage("https://cdn.example.com/main.png", "openai_compatible", "gpt-image-2"),
    )
    fallback = FakeProvider("wanxiang")
    service = ImageGenerationService(
        settings=build_settings(),
        providers={primary.name: primary, fallback.name: fallback},
    )

    result = await service.generate(ImageGenerationRequest(prompt="家具海报"))

    assert result.provider == "openai_compatible"
    assert result.fallback_used is False
    assert len(fallback.calls) == 0


@pytest.mark.asyncio
async def test_temporary_primary_failure_uses_wanxiang():
    """中转站临时故障时使用同一请求调用万相。"""
    from app.services.image_generation_models import (
        GeneratedImage,
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService

    primary = FakeProvider(
        "openai_compatible",
        error=ImageProviderError(
            "timeout",
            category=ImageErrorCategory.TEMPORARY,
            provider="openai_compatible",
        ),
    )
    fallback = FakeProvider(
        "wanxiang",
        result=GeneratedImage("https://dashscope.example.com/fallback.png", "wanxiang", "wanx"),
    )
    service = ImageGenerationService(
        settings=build_settings(),
        providers={primary.name: primary, fallback.name: fallback},
    )
    request = ImageGenerationRequest(prompt="家具海报")

    result = await service.generate(request)

    assert result.provider == "wanxiang"
    assert result.fallback_used is True
    assert fallback.calls == [request]


@pytest.mark.asyncio
async def test_authentication_failure_does_not_call_wanxiang():
    """密钥无效时必须直接失败，防止错误配置被备用模型长期掩盖。"""
    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService

    auth_error = ImageProviderError(
        "forbidden",
        category=ImageErrorCategory.AUTHENTICATION,
        provider="openai_compatible",
    )
    primary = FakeProvider("openai_compatible", error=auth_error)
    fallback = FakeProvider("wanxiang")
    service = ImageGenerationService(
        settings=build_settings(),
        providers={primary.name: primary, fallback.name: fallback},
    )

    with pytest.raises(ImageProviderError) as error_info:
        await service.generate(ImageGenerationRequest(prompt="家具海报"))

    assert error_info.value is auth_error
    assert len(fallback.calls) == 0


@pytest.mark.asyncio
async def test_both_provider_failures_are_reported_without_secret_data():
    """主备均失败时异常需包含两端摘要，但不能泄露鉴权数据。"""
    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import (
        ImageGenerationFallbackError,
        ImageGenerationService,
    )

    primary = FakeProvider(
        "openai_compatible",
        error=ImageProviderError(
            "gateway timeout",
            category=ImageErrorCategory.TEMPORARY,
            provider="openai_compatible",
        ),
    )
    fallback = FakeProvider(
        "wanxiang",
        error=ImageProviderError(
            "task failed",
            category=ImageErrorCategory.UPSTREAM,
            provider="wanxiang",
        ),
    )
    service = ImageGenerationService(
        settings=build_settings(),
        providers={primary.name: primary, fallback.name: fallback},
    )

    with pytest.raises(ImageGenerationFallbackError) as error_info:
        await service.generate(ImageGenerationRequest(prompt="家具海报"))

    message = str(error_info.value)
    assert "openai_compatible" in message
    assert "wanxiang" in message
    assert "sk-" not in message


@pytest.mark.asyncio
async def test_three_level_chain_reaches_wanxiang_after_two_relay_failures():
    """快站与旧中转站均临时失败后，必须把同一请求交给万相兜底。"""
    from app.services.image_generation_models import (
        GeneratedImage,
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService

    settings = SimpleNamespace(
        image_generation_provider="kuai_openai_compatible",
        image_generation_fallback_provider="wanxiang",
        image_generation_provider_chain=(
            "kuai_openai_compatible,openai_compatible,wanxiang"
        ),
    )
    kuai = FakeProvider(
        "kuai_openai_compatible",
        error=ImageProviderError(
            "kuai timeout",
            category=ImageErrorCategory.TEMPORARY,
            provider="kuai_openai_compatible",
        ),
    )
    legacy_relay = FakeProvider(
        "openai_compatible",
        error=ImageProviderError(
            "legacy upstream unavailable",
            category=ImageErrorCategory.UPSTREAM,
            provider="openai_compatible",
        ),
    )
    wanxiang = FakeProvider(
        "wanxiang",
        result=GeneratedImage(
            "https://dashscope.example.com/final.png",
            "wanxiang",
            "wanx2.1-imageedit",
        ),
    )
    service = ImageGenerationService(
        settings=settings,
        providers={
            kuai.name: kuai,
            legacy_relay.name: legacy_relay,
            wanxiang.name: wanxiang,
        },
    )
    request = ImageGenerationRequest(prompt="家具场景图")

    result = await service.generate(request)

    assert result.provider == "wanxiang"
    assert result.fallback_used is True
    assert kuai.calls == [request]
    assert legacy_relay.calls == [request]
    assert wanxiang.calls == [request]


@pytest.mark.asyncio
async def test_secondary_relay_success_skips_wanxiang():
    """快站失败而旧中转站成功时不能继续调用万相，避免同一图片重复计费。"""
    from app.services.image_generation_models import (
        GeneratedImage,
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService

    settings = SimpleNamespace(
        image_generation_provider="kuai_openai_compatible",
        image_generation_fallback_provider="wanxiang",
        image_generation_provider_chain=(
            "kuai_openai_compatible,openai_compatible,wanxiang"
        ),
    )
    kuai = FakeProvider(
        "kuai_openai_compatible",
        error=ImageProviderError(
            "temporary",
            category=ImageErrorCategory.TEMPORARY,
            provider="kuai_openai_compatible",
        ),
    )
    legacy_relay = FakeProvider(
        "openai_compatible",
        result=GeneratedImage(
            "https://legacy.example.com/result.png",
            "openai_compatible",
            "gpt-image-2",
        ),
    )
    wanxiang = FakeProvider("wanxiang")
    service = ImageGenerationService(
        settings=settings,
        providers={
            kuai.name: kuai,
            legacy_relay.name: legacy_relay,
            wanxiang.name: wanxiang,
        },
    )

    result = await service.generate(ImageGenerationRequest(prompt="家具场景图"))

    assert result.provider == "openai_compatible"
    assert result.fallback_used is True
    assert len(wanxiang.calls) == 0


@pytest.mark.asyncio
async def test_ark_is_final_fallback_after_both_relays_fail():
    """两层中转站不可用时应交由方舟，万相不得参与本次生成。

    该回归用例约束实际计费链路：方舟作为万相的替代兜底，而不是在方舟
    成功后仍继续调用历史万相服务。
    """
    from app.services.image_generation_models import (
        GeneratedImage,
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService

    settings = SimpleNamespace(
        image_generation_provider="kuai_openai_compatible",
        image_generation_fallback_provider="volcengine_ark",
        image_generation_provider_chain=(
            "kuai_openai_compatible,openai_compatible,volcengine_ark"
        ),
    )
    kuai = FakeProvider(
        "kuai_openai_compatible",
        error=ImageProviderError(
            "primary unavailable",
            category=ImageErrorCategory.TEMPORARY,
            provider="kuai_openai_compatible",
        ),
    )
    legacy_relay = FakeProvider(
        "openai_compatible",
        error=ImageProviderError(
            "secondary unavailable",
            category=ImageErrorCategory.UPSTREAM,
            provider="openai_compatible",
        ),
    )
    ark = FakeProvider(
        "volcengine_ark",
        result=GeneratedImage(
            "https://storage.example.com/ark.png",
            "volcengine_ark",
            "doubao-seedream-4-0-250828",
        ),
    )
    wanxiang = FakeProvider("wanxiang")
    service = ImageGenerationService(
        settings=settings,
        providers={
            kuai.name: kuai,
            legacy_relay.name: legacy_relay,
            ark.name: ark,
            wanxiang.name: wanxiang,
        },
    )
    request = ImageGenerationRequest(prompt="家具场景图")

    result = await service.generate(request)

    assert result.provider == "volcengine_ark"
    assert result.fallback_used is True
    assert kuai.calls == [request]
    assert legacy_relay.calls == [request]
    assert ark.calls == [request]
    assert wanxiang.calls == []
