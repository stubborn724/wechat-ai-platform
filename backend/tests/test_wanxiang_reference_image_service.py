"""万相参考图片尺寸标准化测试。"""

from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def reset_test_tables():
    """图片处理测试只使用内存字节，不访问数据库。"""
    yield


def make_jpeg(width: int, height: int) -> bytes:
    """生成指定尺寸的纯色 JPEG，隔离外部图片文件依赖。"""
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(220, 220, 220)).save(buffer, format="JPEG")
    return buffer.getvalue()


def read_size(data: bytes) -> tuple[int, int]:
    """读取处理结果尺寸并及时关闭 Pillow 对象。"""
    with Image.open(BytesIO(data)) as image:
        return image.size


def test_small_reference_image_is_scaled_to_minimum_side():
    """400×400 ERP 缩略图应等比放大到万相允许的最小 512×512。"""
    from app.services.wanxiang_reference_image_service import normalize_reference_image

    result = normalize_reference_image(make_jpeg(400, 400), "image/jpeg")

    assert read_size(result.data) == (512, 512)
    assert result.content_type == "image/jpeg"
    assert result.was_transformed is True


def test_compliant_reference_image_keeps_original_bytes():
    """尺寸已合规时不重新编码，避免无意义的产品细节损失。"""
    from app.services.wanxiang_reference_image_service import normalize_reference_image

    original = make_jpeg(800, 600)
    result = normalize_reference_image(original, "image/jpeg")

    assert result.data is original
    assert (result.width, result.height) == (800, 600)
    assert result.was_transformed is False


def test_extreme_aspect_ratio_is_padded_without_cropping():
    """极端长图缩至最大边后应补足短边，不能裁掉产品主体。"""
    from app.services.wanxiang_reference_image_service import normalize_reference_image

    result = normalize_reference_image(make_jpeg(5000, 200), "image/jpeg")

    width, height = read_size(result.data)
    assert 512 <= width <= 4096
    assert 512 <= height <= 4096
    assert width == 4096
    assert height == 512
