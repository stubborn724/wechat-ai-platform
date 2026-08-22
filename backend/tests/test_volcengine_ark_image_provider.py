"""火山方舟图生图适配器测试。"""

import base64
from types import SimpleNamespace

import pytest


class FakeResponse:
    """模拟方舟的 JSON 响应，避免测试产生真实模型费用。"""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        """返回预设响应体。"""
        return self._payload


class FakeAsyncClient:
    """记录请求参数的最小异步 HTTP 客户端。"""

    def __init__(self, response, captured, **_kwargs):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        """支持异步上下文管理协议。"""
        return self

    async def __aexit__(self, *_args):
        """测试替身没有待关闭连接。"""

    async def post(self, url, **kwargs):
        """保存调用参数，用于断言图片字节没有被降级为公网 URL。"""
        self.captured["url"] = url
        self.captured.update(kwargs)
        return self.response


class FakeStorage:
    """模拟本地对象存储，验证豆包输出会立即归档。"""

    def __init__(self):
        self.uploaded = []

    def upload_bytes(self, object_key, data, content_type):
        """记录待归档图片。"""
        self.uploaded.append((object_key, data, content_type))

    def get_url(self, object_key):
        """返回稳定内部素材地址。"""
        return f"http://minio.test/wechat-assets/{object_key}"


def test_ark_size_meets_seedream_minimum_pixel_requirement():
    """方舟 Seedream 4.5 拒绝低于 3686400 像素的请求，兜底规格必须预先抬升。"""
    from app.services.volcengine_ark_image_provider import _normalize_ark_size

    for requested_size in ("1024*1024", "1024*1365", "1365*1024"):
        normalized = _normalize_ark_size(requested_size)
        width, height = (int(part) for part in normalized.split("x", maxsplit=1))
        assert width * height >= 3_686_400


@pytest.mark.asyncio
async def test_ark_provider_sends_local_reference_as_data_uri_and_archives_b64_result():
    """豆包兜底必须直接传本地图片字节，不能要求万相式公网参考图。"""
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.volcengine_ark_image_provider import VolcengineArkImageProvider

    captured = {}
    storage = FakeStorage()
    output = b"generated-image-bytes"
    response = FakeResponse({
        "data": [{
            "b64_json": base64.b64encode(output).decode("ascii"),
            "size": "1024x1536",
        }],
    })

    def client_factory(**kwargs):
        """注入可观测的 HTTP 测试替身。"""
        return FakeAsyncClient(response, captured, **kwargs)

    settings = SimpleNamespace(
        image_generation_ark_base_url="https://ark.example.test/api/v3",
        image_generation_ark_api_key="test-ark-key",
        image_generation_ark_model="doubao-seedream-4-0-250828",
        image_generation_timeout_seconds=30,
        image_generation_max_response_bytes=1024 * 1024,
    )
    provider = VolcengineArkImageProvider(
        settings=settings,
        storage=storage,
        client_factory=client_factory,
        object_key_factory=lambda _tenant_id: "generated-images/107/ark-test.png",
    )

    result = await provider.generate(ImageGenerationRequest(
        prompt="保留家具主体，仅替换背景",
        tenant_id=107,
        reference_image_bytes=b"local-erp-reference",
        reference_content_type="image/jpeg",
        # 即使带有 URL，豆包也应优先使用本地字节，避免公网中转成为依赖。
        reference_image_url="https://temporary.example.test/reference.jpg",
        size="1024*1365",
    ))

    assert captured["url"] == "https://ark.example.test/api/v3/images/generations"
    assert captured["json"]["model"] == "doubao-seedream-4-0-250828"
    assert captured["json"]["watermark"] is False
    assert captured["json"]["response_format"] == "b64_json"
    assert captured["json"]["image"].startswith("data:image/jpeg;base64,")
    assert "temporary.example.test" not in captured["json"]["image"]
    assert storage.uploaded[0][1] == output
    assert result.provider == "volcengine_ark"
    assert result.model == "doubao-seedream-4-0-250828"
    assert result.url.endswith("generated-images/107/ark-test.png")
