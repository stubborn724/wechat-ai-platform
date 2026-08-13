"""定时任务水印快照的契约测试。

任务水印必须是任务自己的配置快照，不能在执行时重新读取租户全局配置；否则
运营人员修改全局水印后，已经验证过的定时任务会悄悄换样式。这里先固定配置
规范化和渲染计划的行为，再由发布链路接入这些纯函数。
"""

from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def reset_test_tables():
    """这些测试只验证配置和纯归档参数，不需要连接或清空业务数据库。"""
    yield


def test_normalize_task_watermark_config_keeps_the_locked_text_snapshot():
    """当前任务的右下角文字水印应保留内容、字号和固定位置。"""
    from app.services.scheduled_task_watermark_service import (
        normalize_task_watermark_config,
    )

    result = normalize_task_watermark_config(
        {
            "enabled": True,
            "type": "text",
            "content": "绣蔓家具 TEL:18682130473",
            "font_size": 24,
            "position": "bottom-right",
            "locked": True,
        }
    )

    assert result["enabled"] is True
    assert result["type"] == "text"
    assert result["content"] == "绣蔓家具 TEL:18682130473"
    assert result["font_size"] == 24
    assert result["position"] == "bottom-right"
    assert result["locked"] is True


def test_normalize_task_watermark_config_rejects_incomplete_logo_snapshot():
    """Logo 快照缺少对象存储键时必须在保存前报错，不能生成无水印图片。"""
    from app.services.scheduled_task_watermark_service import (
        normalize_task_watermark_config,
    )

    with pytest.raises(ValueError, match="image_key"):
        normalize_task_watermark_config(
            {
                "enabled": True,
                "type": "logo",
                "locked": True,
            }
        )


@pytest.mark.asyncio
async def test_final_article_archive_passes_task_watermark_snapshot_to_archive(monkeypatch):
    """最终图片归档必须携带任务快照，避免执行时退回租户全局水印。"""
    from types import SimpleNamespace

    from app.services import article_publication_polish_service as polish

    captured = []

    async def fake_archive(db, tenant_id, image_url, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(storage_key="assets/107/task-watermark.jpg")

    monkeypatch.setattr(
        "app.services.asset_archive_service.save_image_to_asset_library",
        fake_archive,
    )
    monkeypatch.setattr(
        "app.services.storage_service.storage_service.get_url",
        lambda key: f"http://localhost:9002/wechat-assets/{key}",
    )

    task_watermark_config = {
        "enabled": True,
        "type": "text",
        "content": "绣蔓家具 TEL:18682130473",
        "font_size": 24,
        "position": "bottom-right",
        "locked": True,
    }

    await polish.normalize_final_article_images_with_attribution(
        db=SimpleNamespace(),
        content='<article><img src="https://videos.tpkcur.xyz/a.png"/></article>',
        tenant_id=107,
        product_name="异形茶几",
        target_size=(1024, 1365),
        task_watermark_config=task_watermark_config,
    )

    captured_config = captured[0]["task_watermark_config"]
    assert captured_config["enabled"] is True
    assert captured_config["type"] == "text"
    assert captured_config["content"] == task_watermark_config["content"]
    assert captured_config["font_size"] == 24
    assert captured_config["position"] == "bottom-right"
    assert captured_config["locked"] is True
    assert captured[0]["watermark_enabled"] is True
    assert captured[0]["watermark_font_size"] == 24
    assert captured[0]["article_image_attribution"].lines == (
        "绣蔓家具 TEL:18682130473",
    )


@pytest.mark.asyncio
async def test_archive_task_snapshot_does_not_read_tenant_global_watermark(monkeypatch):
    """任务快照存在时，只绘制快照水印，不能再叠加租户当前全局样式。"""
    from app.services import asset_archive_service

    original = Image.new("RGB", (160, 160), "#d9d9d9")
    source_buffer = BytesIO()
    original.save(source_buffer, format="PNG")

    class FakeResponse:
        headers = {"Content-Type": "image/png"}
        content = source_buffer.getvalue()

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return FakeResponse()

    class FakeDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("任务水印快照存在时不应查询租户全局水印")

        def add(self, asset):
            self.asset = asset

        def flush(self):
            self.asset.id = 1

        def commit(self):
            return None

    uploaded = []

    monkeypatch.setattr(asset_archive_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        asset_archive_service,
        "generate_object_key",
        lambda *_args, **_kwargs: "assets/auto/task-watermark.png",
    )
    monkeypatch.setattr(
        asset_archive_service.storage_service,
        "upload_bytes",
        lambda **kwargs: uploaded.append(kwargs),
    )

    result = await asset_archive_service.save_image_to_asset_library(
        FakeDb(),
        107,
        "https://videos.tpkcur.xyz/task-watermark.png",
        task_watermark_config={
            "enabled": True,
            "type": "text",
            "content": "绣蔓家具 TEL:18682130473",
            "font_size": 24,
            "position": "bottom-right",
            "locked": True,
        },
    )

    assert result is not None
    assert uploaded
    assert uploaded[0]["data"] != source_buffer.getvalue()


@pytest.mark.asyncio
async def test_archive_stops_when_required_task_text_watermark_cannot_be_rendered(monkeypatch):
    """启用任务文字水印时，绘制失败不得上传无水印图片供后续发布。"""

    from app.services import asset_archive_service

    original = Image.new("RGB", (160, 160), "#d9d9d9")
    source_buffer = BytesIO()
    original.save(source_buffer, format="PNG")

    class FakeDb:
        def add(self, _asset):
            raise AssertionError("水印失败时不得创建素材记录")

    uploaded = []
    monkeypatch.setattr(
        asset_archive_service.storage_service,
        "upload_bytes",
        lambda **kwargs: uploaded.append(kwargs),
    )
    monkeypatch.setattr(
        "app.services.article_publication_polish_service.apply_article_image_attribution_to_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("font unavailable")),
    )

    result = await asset_archive_service.save_image_to_asset_library(
        FakeDb(),
        107,
        "",
        image_bytes=source_buffer.getvalue(),
        image_content_type="image/png",
        task_watermark_config={
            "enabled": True,
            "type": "text",
            "content": "绣蔓家具 TEL:18682130473",
            "font_size": 24,
            "position": "bottom-right",
            "locked": True,
        },
    )

    assert result is None
    assert uploaded == []


@pytest.mark.asyncio
async def test_archive_stops_when_required_task_logo_cannot_be_loaded(monkeypatch):
    """任务 Logo 丢失时不能让渲染器返回原图并继续上传。"""

    from app.services import asset_archive_service

    original = Image.new("RGB", (160, 160), "#d9d9d9")
    source_buffer = BytesIO()
    original.save(source_buffer, format="PNG")

    class FakeDb:
        def add(self, _asset):
            raise AssertionError("Logo 水印失败时不得创建素材记录")

    uploaded = []
    monkeypatch.setattr(
        asset_archive_service.storage_service,
        "upload_bytes",
        lambda **kwargs: uploaded.append(kwargs),
    )
    monkeypatch.setattr(
        asset_archive_service.storage_service,
        "download_bytes",
        lambda _key: (_ for _ in ()).throw(FileNotFoundError("logo not found")),
    )

    result = await asset_archive_service.save_image_to_asset_library(
        FakeDb(),
        107,
        "",
        image_bytes=source_buffer.getvalue(),
        image_content_type="image/png",
        task_watermark_config={
            "enabled": True,
            "type": "logo",
            "image_key": "assets/107/missing-logo.png",
            "position": "bottom-right",
            "locked": True,
        },
    )

    assert result is None
    assert uploaded == []
