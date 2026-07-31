"""万相图像编辑参考图的尺寸与编码标准化服务。

该模块只处理模型输入约束，不负责云存储或任务编排。ERP 原图继续原样保存在
MinIO；仅上传 COS 的临时副本在必要时被等比缩放或补边，避免改变长期素材。
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image


MIN_REFERENCE_SIDE = 512
MAX_REFERENCE_SIDE = 4096


@dataclass(frozen=True)
class NormalizedReferenceImage:
    """可直接交给万相图像编辑接口的图片字节及元数据。"""

    data: bytes
    content_type: str
    width: int
    height: int
    was_transformed: bool


def normalize_reference_image(data: bytes, content_type: str) -> NormalizedReferenceImage:
    """将参考图规范到万相允许的 512～4096 像素范围。

    合规图片返回原始字节，避免重复压缩；不合规图片先等比缩放，极端宽高比无法
    同时满足上下限时使用白色画布居中补边。全流程不裁剪，确保 ERP 产品主体不会
    因尺寸适配被切掉。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        raise ValueError("参考图片必须是非空字节内容")

    original_data = data if isinstance(data, bytes) else bytes(data)
    try:
        with Image.open(BytesIO(original_data)) as source:
            source.load()
            width, height = source.size
            if width <= 0 or height <= 0:
                raise ValueError("参考图片尺寸无效")

            detected_content_type, output_format = _resolve_output_format(
                source.format,
                content_type,
            )
            if _is_dimension_compliant(width, height):
                return NormalizedReferenceImage(
                    data=original_data,
                    content_type=detected_content_type,
                    width=width,
                    height=height,
                    was_transformed=False,
                )

            resized = _resize_within_model_bounds(source, width, height)
            normalized = _pad_short_side_if_needed(resized)
            output_data = _encode_image(normalized, output_format)
            return NormalizedReferenceImage(
                data=output_data,
                content_type=detected_content_type,
                width=normalized.width,
                height=normalized.height,
                was_transformed=True,
            )
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("参考图片无法解码") from exc


def _is_dimension_compliant(width: int, height: int) -> bool:
    """判断两个边长是否都落在万相官方限制范围内。"""
    return (
        MIN_REFERENCE_SIDE <= width <= MAX_REFERENCE_SIDE
        and MIN_REFERENCE_SIDE <= height <= MAX_REFERENCE_SIDE
    )


def _resize_within_model_bounds(source: Image.Image, width: int, height: int) -> Image.Image:
    """等比缩放到最接近原图且不超过最大边的尺寸。"""
    minimum_scale = max(MIN_REFERENCE_SIDE / width, MIN_REFERENCE_SIDE / height)
    maximum_scale = min(MAX_REFERENCE_SIDE / width, MAX_REFERENCE_SIDE / height)

    if max(width, height) > MAX_REFERENCE_SIDE:
        scale = maximum_scale
    else:
        scale = min(minimum_scale, maximum_scale)

    target_width = max(1, min(MAX_REFERENCE_SIDE, round(width * scale)))
    target_height = max(1, min(MAX_REFERENCE_SIDE, round(height * scale)))
    if (target_width, target_height) == (width, height):
        return source.copy()
    return source.resize((target_width, target_height), Image.Resampling.LANCZOS)


def _pad_short_side_if_needed(image: Image.Image) -> Image.Image:
    """对极端宽高比补白边，使短边达标且不裁剪产品。"""
    canvas_width = max(MIN_REFERENCE_SIDE, image.width)
    canvas_height = max(MIN_REFERENCE_SIDE, image.height)
    if (canvas_width, canvas_height) == image.size:
        return image

    # 白底兼容 JPEG 且适合商品图；若源图包含透明通道，先合成再编码，避免黑底。
    canvas = Image.new("RGB", (canvas_width, canvas_height), color=(255, 255, 255))
    converted = image.convert("RGBA")
    position = (
        (canvas_width - image.width) // 2,
        (canvas_height - image.height) // 2,
    )
    canvas.paste(converted, position, converted)
    return canvas


def _resolve_output_format(source_format: str | None, content_type: str) -> tuple[str, str]:
    """将输入格式收敛到万相稳定支持且 Pillow 可编码的格式。"""
    normalized_format = str(source_format or "").upper()
    if normalized_format in {"JPG", "JPEG"}:
        return "image/jpeg", "JPEG"
    if normalized_format == "PNG":
        return "image/png", "PNG"
    if normalized_format == "WEBP":
        return "image/webp", "WEBP"

    # 其他格式统一转 JPEG，避免动图、多帧 TIFF 等不稳定输入进入图像编辑接口。
    return "image/jpeg", "JPEG"


def _encode_image(image: Image.Image, output_format: str) -> bytes:
    """按目标格式编码处理结果，JPEG 显式使用高质量参数保留产品细节。"""
    output = BytesIO()
    if output_format == "JPEG":
        image.convert("RGB").save(output, format="JPEG", quality=95, subsampling=0)
    elif output_format == "PNG":
        image.save(output, format="PNG", optimize=True)
    else:
        image.save(output, format="WEBP", quality=95, method=6)
    return output.getvalue()
