"""图片生成提供商领域对象测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """领域单元测试不访问业务数据库，覆盖全局数据库清理夹具。"""
    yield


def test_temporary_provider_error_is_fallback_eligible():
    """网络和上游临时故障允许切换到备用提供商。"""
    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageProviderError,
    )

    error = ImageProviderError(
        "中转站读取超时",
        category=ImageErrorCategory.TEMPORARY,
        provider="openai_compatible",
    )

    assert error.can_fallback is True


def test_auth_provider_error_is_not_fallback_eligible():
    """鉴权配置错误必须直接暴露，不能长期由万相掩盖。"""
    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageProviderError,
    )

    error = ImageProviderError(
        "中转站拒绝密钥",
        category=ImageErrorCategory.AUTHENTICATION,
        provider="openai_compatible",
    )

    assert error.can_fallback is False


def test_generation_request_rejects_empty_prompt():
    """空提示词属于调用方错误，不应产生付费请求。"""
    from app.services.image_generation_models import ImageGenerationRequest

    with pytest.raises(ValueError, match="提示词"):
        ImageGenerationRequest(prompt="   ")


def test_generation_request_requires_reference_content_type_with_bytes():
    """上传参考图字节时必须携带 MIME 类型，避免 multipart 类型不确定。"""
    from app.services.image_generation_models import ImageGenerationRequest

    with pytest.raises(ValueError, match="MIME"):
        ImageGenerationRequest(
            prompt="只替换背景",
            reference_image_bytes=b"image-bytes",
        )


@pytest.mark.parametrize(
    ("relay_key", "dashscope_key", "expected"),
    [
        ("relay-key", "", True),
        ("", "dashscope-key", True),
        ("", "", False),
    ],
)
def test_image_generation_availability_accepts_primary_or_fallback_key(
    relay_key,
    dashscope_key,
    expected,
):
    """主中转站或万相任一具备密钥时，业务入口都应允许进入统一路由。"""
    from types import SimpleNamespace

    from app.services.image_generation_service import is_image_generation_configured

    configured = is_image_generation_configured(SimpleNamespace(
        image_generation_api_key=relay_key,
        dashscope_api_key=dashscope_key,
    ))

    assert configured is expected
