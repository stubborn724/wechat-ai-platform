"""万相图片生成诊断日志的单元测试。"""

import logging

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """服务测试不访问本地业务数据库。"""
    yield


@pytest.mark.asyncio
async def test_generate_image_logs_http_status_and_response_without_secret(monkeypatch, caplog):
    """HTTP 错误必须输出状态与响应摘要，但绝不能泄露密钥。"""
    import app.services.wanxiang_service as wanxiang_module
    from app.services.wanxiang_service import WanxiangImageService

    class FakeResponse:
        status_code = 400
        text = "invalid model"

        def json(self):
            return {"code": "InvalidParameter", "message": "invalid model"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(wanxiang_module.httpx, "AsyncClient", lambda **_: FakeClient())
    caplog.set_level(logging.INFO, logger="app.services.wanxiang_service")

    service = WanxiangImageService(api_key="secret-value")
    result = await service.generate_image("家具提示词", size="1024*1365")

    assert result is None
    assert "status=400" in caplog.text
    assert "invalid model" in caplog.text
    assert "secret-value" not in caplog.text


@pytest.mark.asyncio
async def test_dashscope_service_returns_none_when_wanxiang_fails(monkeypatch, caplog):
    """仿写图片生成失败时不得回退为随机图库地址。"""
    from app.services.image_service_v2 import DashScopeImageGenService
    from app.services.wanxiang_service import WanxiangImageService

    async def failing_generation(*args, **kwargs):
        return None

    monkeypatch.setattr(WanxiangImageService, "generate_image", failing_generation)
    caplog.set_level(logging.ERROR, logger="app.services.image_service_v2")

    result = await DashScopeImageGenService().search_image(
        "家具",
        prompt="完整的家具视觉提示词",
    )

    assert result is None
    assert "随机图库回退已阻止" in caplog.text


@pytest.mark.asyncio
async def test_reference_image_uses_wanx_image_to_image_model(monkeypatch):
    """ERP 原图必须使用万相图像编辑的官方 endpoint 与字段契约。"""
    import app.services.wanxiang_service as wanxiang_module
    from app.services.wanxiang_service import WanxiangImageService

    captured_request = {}

    class FakeResponse:
        status_code = 400
        text = "stop after request capture"

        def json(self):
            return {"code": "InvalidParameter", "message": self.text}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured_request["url"] = url
            captured_request["body"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr(wanxiang_module.httpx, "AsyncClient", lambda **_: FakeClient())
    service = WanxiangImageService(api_key="test-key")

    await service.generate_image(
        "在高雅客厅展示该家具，保留产品主体",
        reference_image_url="https://erp.example.com/product.jpg",
    )

    body = captured_request["body"]
    assert captured_request["url"].endswith("/services/aigc/image2image/image-synthesis")
    assert body["model"] == "wanx2.1-imageedit"
    assert body["input"]["function"] == "description_edit"
    assert body["input"]["base_image_url"] == "https://erp.example.com/product.jpg"
    assert "ref_img" not in body["input"]
    assert body["parameters"]["strength"] == pytest.approx(0.35)
    assert "size" not in body["parameters"]


@pytest.mark.asyncio
async def test_strategy_returns_none_when_no_image_provider_is_registered(caplog):
    """没有可用图片服务时，同样不得生成随机图库地址。"""
    from app.services.image_service_v2 import ImageServiceStrategy

    strategy = ImageServiceStrategy()
    strategy._services.clear()
    caplog.set_level(logging.ERROR, logger="app.services.image_service_v2")

    assert await strategy.execute("DASHSCOPE", "家具") is None
    assert "随机图库回退已阻止" in caplog.text
