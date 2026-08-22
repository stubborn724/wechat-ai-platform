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
async def test_open_circuit_skips_unhealthy_provider_and_uses_fallback():
    """同一提供商连续临时失败后，后续图片不得再次等待其超时。"""
    from app.services.image_generation_models import (
        GeneratedImage,
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.image_generation_service import ImageGenerationService
    from app.services.image_provider_health_service import ImageProviderHealthService

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
        result=GeneratedImage(
            "https://cdn.example.com/fallback.png",
            "wanxiang",
            "wanx",
        ),
    )
    service = ImageGenerationService(
        settings=build_settings(),
        providers={primary.name: primary, fallback.name: fallback},
        health_service=ImageProviderHealthService(
            failure_threshold=1,
            cooldown_seconds=600,
        ),
    )

    await service.generate(ImageGenerationRequest(prompt="第一张"))
    await service.generate(ImageGenerationRequest(prompt="第二张"))

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2


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
    """开启全量降级时，鉴权失败也必须继续尝试下一层。"""
    from app.services.image_generation_models import (
        GeneratedImage,
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
    fallback = FakeProvider(
        "wanxiang",
        result=GeneratedImage("https://cdn.example.com/fallback.png", "wanxiang", "wanx"),
    )
    service = ImageGenerationService(
        settings=SimpleNamespace(
            **vars(build_settings()),
            image_generation_fallback_on_any_error=True,
        ),
        providers={primary.name: primary, fallback.name: fallback},
    )

    result = await service.generate(ImageGenerationRequest(prompt="家具海报"))

    assert result.provider == "wanxiang"
    assert result.fallback_used is True
    assert fallback.calls


@pytest.mark.asyncio
async def test_unexpected_provider_exception_also_reaches_next_layer():
    """提供商抛出未分类异常时，也不能阻断后续图片模型。"""
    from app.services.image_generation_models import GeneratedImage, ImageGenerationRequest
    from app.services.image_generation_service import ImageGenerationService

    class UnexpectedFailureProvider(FakeProvider):
        async def generate(self, request):
            self.calls.append(request)
            raise RuntimeError("连接池临时异常")

    primary = UnexpectedFailureProvider("openai_compatible")
    fallback = FakeProvider(
        "wanxiang",
        result=GeneratedImage("https://cdn.example.com/fallback.png", "wanxiang", "wanx"),
    )
    service = ImageGenerationService(
        settings=SimpleNamespace(
            **vars(build_settings()),
            image_generation_fallback_on_any_error=True,
        ),
        providers={primary.name: primary, fallback.name: fallback},
    )

    result = await service.generate(ImageGenerationRequest(prompt="家具海报"))

    assert result.provider == "wanxiang"
    assert result.fallback_used is True
    assert primary.calls
    assert fallback.calls


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


def test_default_provider_factory_keeps_two_openai_layers_jiuye_and_ark_fallback():
    """默认图片链路必须含两层 OpenAI、九野异步层与方舟最终兜底。

    两层 Kuai 使用不同 Provider 名称，确保模型级熔断互不影响；旧的第三个
    Kuai Provider 不应继续被装配，避免配置表面上与实际生产链路不一致。
    """
    from app.services.image_generation_service import _build_default_providers

    settings = SimpleNamespace(
        image_generation_base_url="https://api.kuai.host/v1",
        image_generation_api_key="test-key",
        image_generation_model="doubao-seedream-4-5-251128",
        image_generation_edit_model="doubao-seedream-4-5-251128",
        image_generation_secondary_base_url="https://api.kuai.host/v1",
        image_generation_secondary_api_key="test-key",
        image_generation_secondary_model="doubao-seedream-4-0-250828",
        image_generation_secondary_edit_model="doubao-seedream-4-0-250828",
        image_generation_tertiary_base_url="https://api.kuai.host/v1",
        image_generation_tertiary_api_key="test-key",
        image_generation_tertiary_model="doubao-seedream-4-0-250828",
        image_generation_tertiary_edit_model="doubao-seedream-4-0-250828",
        image_generation_jiuye_base_url="https://api.jiuyeyingxiang.com",
        image_generation_jiuye_api_key="jiuye-test-key",
        image_generation_jiuye_model="gpt-image-2",
        image_generation_jiuye_timeout_seconds=240,
        image_generation_jiuye_poll_interval_seconds=3,
        image_generation_timeout_seconds=1800,
        image_generation_primary_timeout_seconds=120,
        image_generation_secondary_timeout_seconds=150,
        image_generation_tertiary_timeout_seconds=180,
        image_generation_ark_base_url="https://ark.example.test/api/v3",
        image_generation_ark_api_key="ark-test-key",
        image_generation_ark_model="doubao-seedream-4-5-251128",
        image_generation_ark_timeout_seconds=180,
        image_generation_max_response_bytes=20 * 1024 * 1024,
    )

    providers = _build_default_providers(settings)

    assert set(providers) == {
        "kuai_openai_compatible",
        "kuai_seedream_40",
        "jiuye_image_2",
        "volcengine_ark",
    }
    assert providers["kuai_openai_compatible"].edit_model == "doubao-seedream-4-5-251128"
    assert providers["kuai_seedream_40"].edit_model == "doubao-seedream-4-0-250828"
    assert providers["jiuye_image_2"].model == "gpt-image-2"
    assert providers["volcengine_ark"].model == "doubao-seedream-4-5-251128"
