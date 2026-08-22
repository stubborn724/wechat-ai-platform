"""九野映像异步图片提供商测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件使用纯 HTTP 与存储替身，不访问业务数据库。"""
    yield


class FakeResponse:
    """提供测试所需的最小 HTTP 响应边界。"""

    def __init__(self, *, status_code=200, payload=None, content=b"", content_type="application/json"):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self):
        """返回预设 JSON 体。"""
        return self._payload


class FakeAsyncClient:
    """按顺序返回提交、轮询和下载响应，验证异步协议没有被当成同步接口。"""

    def __init__(self, responses, captured, **_kwargs):
        self.responses = iter(responses)
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.captured.append(("post", url, kwargs))
        return next(self.responses)

    async def get(self, url, **kwargs):
        self.captured.append(("get", url, kwargs))
        return next(self.responses)


class FakeStorage:
    """记录归档结果，确保临时结果 URL 不会直接进入文章。"""

    def __init__(self):
        self.uploads = []

    def upload_bytes(self, object_key, data, content_type):
        self.uploads.append((object_key, data, content_type))

    def get_url(self, object_key):
        return f"http://minio.test/wechat-assets/{object_key}"


@pytest.mark.asyncio
async def test_jiuye_provider_polls_task_and_archives_completed_image():
    """九野第三层必须提交任务、轮询完成并归档结果图片。"""
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.jiuye_image_provider import JiuyeImageProvider

    captured = []
    storage = FakeStorage()
    responses = [
        FakeResponse(payload={"task_id": "task-123", "status": "pending"}),
        FakeResponse(payload={"task_id": "task-123", "status": "running"}),
        FakeResponse(payload={
            "task_id": "task-123",
            "status": "succeeded",
            "result_url": "https://result.example.test/output.png",
            "result_type": "image",
        }),
        FakeResponse(content=b"png-bytes", content_type="image/png"),
    ]
    settings = SimpleNamespace(
        image_generation_jiuye_base_url="https://api.jiuyeyingxiang.com",
        image_generation_jiuye_api_key="test-key",
        image_generation_jiuye_model="gpt-image-2",
        image_generation_jiuye_timeout_seconds=30,
        image_generation_jiuye_poll_interval_seconds=0,
        image_generation_max_response_bytes=1024 * 1024,
    )
    provider = JiuyeImageProvider(
        settings=settings,
        storage=storage,
        client_factory=lambda **kwargs: FakeAsyncClient(responses, captured, **kwargs),
        object_key_factory=lambda _tenant_id: "generated-images/107/jiuye.png",
    )

    result = await provider.generate(ImageGenerationRequest(
        prompt="保留家具主体，仅替换客厅背景",
        size="1024*1365",
        tenant_id=107,
        reference_image_bytes=b"erp-product-image",
        reference_content_type="image/jpeg",
    ))

    assert captured[0][1] == "https://api.jiuyeyingxiang.com/v1/xingba/image"
    assert captured[0][2]["json"]["model"] == "gpt-image-2"
    assert captured[0][2]["json"]["aspectRatio"] == "3:4"
    assert captured[0][2]["json"]["images"][0].startswith("data:image/jpeg;base64,")
    assert [call[1] for call in captured[1:3]] == [
        "https://api.jiuyeyingxiang.com/v1/xingba/image/task-123",
        "https://api.jiuyeyingxiang.com/v1/xingba/image/task-123",
    ]
    assert captured[3][1] == "https://result.example.test/output.png"
    assert storage.uploads == [("generated-images/107/jiuye.png", b"png-bytes", "image/png")]
    assert result.provider == "jiuye_image_2"
    assert result.model == "gpt-image-2"
