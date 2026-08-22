"""定时任务生成图片的视觉质量边界测试。

这些测试只验证程序能够拦截真正的空白/低信息量图片，同时放行有商品主体的
白底商品图。这样“背景不能空白”和“白底商品图可以使用”两个需求不会互相冲突。
"""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw


@pytest.fixture(autouse=True)
def reset_test_tables():
    """图片质量判断是纯内存逻辑，不应连接业务数据库。"""
    yield


def _image_bytes(image: Image.Image, image_format: str = "PNG") -> bytes:
    """把测试图片编码成生产服务接收的原始字节。"""
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_assess_image_bytes_rejects_uniform_blank_image():
    """纯白结果没有可交付的视觉信息，必须在发布前被识别。"""
    from app.services.scheduled_image_quality_service import assess_image_bytes

    report = assess_image_bytes(_image_bytes(Image.new("RGB", (1024, 1365), "white")))

    assert report.is_usable is False
    assert "低信息量" in report.reason


def test_assess_image_bytes_accepts_white_background_product_image():
    """商品主体、阴影和材质细节存在时，即使背景偏白也不能误判为空白。"""
    image = Image.new("RGB", (1024, 1365), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((210, 930, 820, 1080), fill=(190, 190, 190))
    draw.rounded_rectangle((180, 380, 850, 930), radius=48, fill=(108, 72, 48))
    draw.rectangle((240, 430, 790, 820), fill=(232, 220, 194))

    from app.services.scheduled_image_quality_service import assess_image_bytes

    report = assess_image_bytes(_image_bytes(image))

    assert report.is_usable is True


def test_append_scene_quality_guard_is_idempotent_and_requires_real_background():
    """场景硬约束只能追加一次，并明确禁止抠图式纯色空背景。"""
    from app.services.scheduled_image_quality_service import (
        append_scene_quality_guard,
    )

    prompt = append_scene_quality_guard("主体：实木餐桌")
    repeated = append_scene_quality_guard(prompt)

    assert "真实空间层次" in prompt
    assert "纯白、纯灰或纯色空背景" in prompt
    assert repeated == prompt


@pytest.mark.asyncio
async def test_inspect_generated_image_url_reads_local_minio_through_internal_storage(monkeypatch):
    """Docker Worker 检查本地 MinIO 图片时必须走内部客户端而不是 localhost HTTP。

    生成服务返回的宿主机地址在容器中无法访问；质量检查仍应读取同一个桶里的
    对象字节，避免备用图片模型生成成功后因地址拓扑不同被误判为失败。
    """
    from app.services import scheduled_image_quality_service as quality_service

    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 76, 76), fill=(80, 60, 40))
    expected_bytes = _image_bytes(image)

    class FakeStorage:
        """记录内部读取的对象键，确保没有把本地 URL交给 HTTP 客户端。"""

        def __init__(self):
            self.object_keys = []

        def download_bytes(self, object_key):
            self.object_keys.append(object_key)
            return expected_bytes

    storage = FakeStorage()
    monkeypatch.setattr(quality_service, "storage_service", storage)

    report = await quality_service.inspect_generated_image_url(
        "http://localhost:9002/wechat-assets/generated-images/107/result.png"
    )

    assert report.is_usable is True
    assert storage.object_keys == ["generated-images/107/result.png"]


@pytest.mark.asyncio
async def test_inspect_generated_image_url_marks_download_failure_retryable(monkeypatch):
    """临时下载失败应交给定时任务整体重试，而不是只按低质量图片结束。"""
    from app.services import scheduled_image_quality_service as quality_service

    class FailingStorage:
        """模拟 MinIO 短暂断连；这里不访问真实存储，避免测试产生外部副作用。"""

        def download_bytes(self, _object_key):
            raise ConnectionError("MinIO 暂时不可达")

    monkeypatch.setattr(quality_service, "storage_service", FailingStorage())

    report = await quality_service.inspect_generated_image_url(
        "http://localhost:9002/wechat-assets/generated-images/107/result.png"
    )

    assert report.is_usable is False
    assert report.retryable is True
