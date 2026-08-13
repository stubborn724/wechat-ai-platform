"""定时任务图片画布归一化的回归测试。

定时 ERP 图片最终会进入公众号正文和水印后处理。这里先锁定“输出尺寸固定、
不改变原图内容比例”的边界，防止不同图片提供商返回不同尺寸后再次影响水印
位置和视觉比例。
"""

from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def reset_test_tables():
    """画布归一化是纯内存逻辑，不应触发项目级数据库清理夹具。"""

    yield


def _image_bytes(size: tuple[int, int], image_format: str = "PNG") -> bytes:
    """创建测试图片字节，隔离真实图片下载和对象存储。"""

    image = Image.new("RGB", size, "#d9d9d9")
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_normalize_scheduled_image_bytes_returns_the_canonical_portrait_canvas():
    """供应商返回任意比例时，定时图片仍必须落成 1024×1365 画布。"""

    from app.services.scheduled_image_normalization_service import (
        CANONICAL_SCHEDULED_IMAGE_SIZE,
        normalize_scheduled_image_bytes,
    )

    result = normalize_scheduled_image_bytes(
        _image_bytes((1536, 1024)),
        content_type="image/png",
    )

    assert result.size == CANONICAL_SCHEDULED_IMAGE_SIZE == (1024, 1365)
    with Image.open(BytesIO(result.data)) as normalized:
        assert normalized.size == (1024, 1365)


def test_normalize_scheduled_image_bytes_is_idempotent_for_canonical_images():
    """已经是标准尺寸的图片再次经过归一化不能继续缩放或改变画布。"""

    from app.services.scheduled_image_normalization_service import (
        normalize_scheduled_image_bytes,
    )

    source = _image_bytes((1024, 1365))
    result = normalize_scheduled_image_bytes(source, content_type="image/png")

    assert result.size == (1024, 1365)
    with Image.open(BytesIO(result.data)) as normalized:
        assert normalized.size == (1024, 1365)
