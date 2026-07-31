"""OpenAI 兼容图片中转站适配器测试。"""

import base64
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """适配器测试使用内存替身，不访问业务数据库。"""
    yield


class FakeResponse:
    """只实现适配器读取的 HTTP 响应边界。"""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"response"

    def json(self):
        return self._payload


class FakeHttpClient:
    """记录最后一次请求，便于验证 JSON 与 multipart 协议。"""

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeStorage:
    """记录归档字节并返回稳定的本地素材 URL。"""

    def __init__(self):
        self.uploads = []

    def upload_bytes(self, object_name, data, content_type):
        self.uploads.append((object_name, data, content_type))

    def get_url(self, object_name):
        return f"http://localhost:9002/wechat-assets/{object_name}"


def build_settings(**overrides):
    """构造不含真实密钥的最小配置替身。"""
    values = {
        "image_generation_base_url": "https://relay.example.com/v1",
        "image_generation_api_key": "test-key",
        "image_generation_model": "gpt-image-2",
        "image_generation_edit_model": "gpt-image-2",
        "image_generation_timeout_seconds": 240,
        "image_generation_max_response_bytes": 20 * 1024 * 1024,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_text_generation_posts_model_and_complete_prompt():
    """文本生图必须把结构化合成后的完整提示词原样传给主模型。"""
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.openai_compatible_image_provider import OpenAICompatibleImageProvider

    client = FakeHttpClient(FakeResponse({"data": [{"url": "https://cdn.example.com/result.png"}]}))
    provider = OpenAICompatibleImageProvider(
        settings=build_settings(),
        storage=FakeStorage(),
        client_factory=lambda **_: client,
    )

    result = await provider.generate(
        ImageGenerationRequest(prompt="构图、镜头、光影与新主体的完整提示词")
    )

    url, kwargs = client.calls[0]
    assert url == "https://relay.example.com/v1/images/generations"
    assert kwargs["json"]["model"] == "gpt-image-2"
    assert kwargs["json"]["prompt"] == "构图、镜头、光影与新主体的完整提示词"
    assert result.url == "https://cdn.example.com/result.png"


@pytest.mark.asyncio
async def test_reference_edit_uploads_image_bytes_as_multipart():
    """参考图编辑必须上传原图字节，不能只把产品名写入提示词。"""
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.openai_compatible_image_provider import OpenAICompatibleImageProvider

    client = FakeHttpClient(FakeResponse({"data": [{"url": "https://cdn.example.com/edit.png"}]}))
    provider = OpenAICompatibleImageProvider(
        settings=build_settings(),
        storage=FakeStorage(),
        client_factory=lambda **_: client,
    )

    await provider.generate(ImageGenerationRequest(
        prompt="严格保留家具主体，只替换背景",
        size="1024*1365",
        reference_image_bytes=b"reference-image",
        reference_content_type="image/png",
    ))

    url, kwargs = client.calls[0]
    assert url == "https://relay.example.com/v1/images/edits"
    assert kwargs["data"]["model"] == "gpt-image-2"
    assert kwargs["files"]["image"][1] == b"reference-image"
    assert kwargs["files"]["image"][2] == "image/png"


@pytest.mark.asyncio
async def test_data_uri_result_is_decoded_and_archived_to_minio():
    """中转站数据 URI 必须解码归档，文章结果不能保留超长 Base64。"""
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.openai_compatible_image_provider import OpenAICompatibleImageProvider

    image_bytes = b"generated-png-bytes"
    data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    storage = FakeStorage()
    client = FakeHttpClient(FakeResponse({"data": [{"url": data_uri}]}))
    provider = OpenAICompatibleImageProvider(
        settings=build_settings(),
        storage=storage,
        client_factory=lambda **_: client,
    )

    result = await provider.generate(ImageGenerationRequest(prompt="生成家具海报", tenant_id=107))

    assert result.url.startswith("http://localhost:9002/wechat-assets/generated-images/107/")
    assert storage.uploads[0][1] == image_bytes
    assert storage.uploads[0][2] == "image/png"


@pytest.mark.asyncio
async def test_authentication_error_is_classified_as_non_fallback_error():
    """中转站 401/403 属于配置问题，统一路由不得静默切换万相。"""
    from app.services.image_generation_models import (
        ImageErrorCategory,
        ImageGenerationRequest,
        ImageProviderError,
    )
    from app.services.openai_compatible_image_provider import OpenAICompatibleImageProvider

    client = FakeHttpClient(FakeResponse(
        {"error": {"message": "invalid token"}},
        status_code=403,
    ))
    provider = OpenAICompatibleImageProvider(
        settings=build_settings(),
        storage=FakeStorage(),
        client_factory=lambda **_: client,
    )

    with pytest.raises(ImageProviderError) as error_info:
        await provider.generate(ImageGenerationRequest(prompt="生成家具海报"))

    assert error_info.value.category == ImageErrorCategory.AUTHENTICATION
    assert error_info.value.can_fallback is False
