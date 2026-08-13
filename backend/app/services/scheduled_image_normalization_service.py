"""定时任务图片的统一画布处理服务。

ERP 图生图供应商可能忽略请求尺寸，返回 1024×1536、864×1821 或其他比例的
图片。尺寸不统一会让公众号中的图片高度、右下角水印和多图浏览比例随图片变化。
本模块只负责把新生成的定时图片收口到稳定画布，不参与普通文章和历史素材处理。
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps


# 当前用户确认的定时 ERP 图片规格。宽度固定后，24px 水印在每张新图上具有相同
# 的视觉比例；高度采用 1365，避免 1536 高图在公众号中显得过于细长。
CANONICAL_SCHEDULED_IMAGE_SIZE: tuple[int, int] = (1024, 1365)
SCHEDULED_WATERMARK_FONT_SIZE = 24


@dataclass(frozen=True)
class NormalizedScheduledImage:
    """图片归一化结果。

    ``data`` 是已经落到目标画布的图片字节，``size`` 用于更新素材库元数据，
    ``content_type`` 确保归一化后对象存储的 MIME 与实际编码保持一致。
    """

    data: bytes
    size: tuple[int, int]
    content_type: str


def normalize_scheduled_image_bytes(
    image_bytes: bytes,
    *,
    content_type: str = "image/jpeg",
    target_size: tuple[int, int] = CANONICAL_SCHEDULED_IMAGE_SIZE,
) -> NormalizedScheduledImage:
    """将定时图片裁切到固定画布并保持原图比例。

    使用 ``ImageOps.fit`` 而不是直接拉伸：图片会按比例缩放后从中心裁切多余
    边缘，家具主体不会被横向或纵向拉变形。该策略只在 ERP 定时图的最终归档
    阶段启用，普通文章仍保留原始图片尺寸。透明图会合成为 RGB，保证微信正文
    不出现透明棋盘格，也让水印层与最终像素使用同一个颜色空间。
    """

    if not image_bytes:
        raise ValueError("定时图片字节不能为空")

    width, height = target_size
    if width < 1 or height < 1:
        raise ValueError("定时图片目标尺寸必须为正整数")

    with Image.open(io.BytesIO(image_bytes)) as source:
        source.load()
        rgb_source = source.convert("RGB")
        normalized = ImageOps.fit(
            rgb_source,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    output_type = _normalized_content_type(content_type)
    output_format = _content_type_to_format(output_type)
    output = io.BytesIO()
    save_kwargs: dict[str, object] = {"format": output_format}
    if output_format == "JPEG":
        save_kwargs.update({"quality": 94, "optimize": True})
    normalized.save(output, **save_kwargs)

    return NormalizedScheduledImage(
        data=output.getvalue(),
        size=(width, height),
        content_type=output_type,
    )


def _normalized_content_type(content_type: str) -> str:
    """把输入 MIME 收敛到 Pillow 支持且适合微信图片的三种格式。"""

    normalized = str(content_type or "").lower()
    if "png" in normalized:
        return "image/png"
    if "webp" in normalized:
        return "image/webp"
    return "image/jpeg"


def _content_type_to_format(content_type: str) -> str:
    """将归一化 MIME 转成 Pillow 编码名称。"""

    if content_type == "image/png":
        return "PNG"
    if content_type == "image/webp":
        return "WEBP"
    return "JPEG"
