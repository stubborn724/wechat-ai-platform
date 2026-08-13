"""水印处理服务 — 图片 LOGO 水印叠加"""

import io
import logging
import os
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class WatermarkService:
    """图片/视频水印处理"""

    def __init__(self):
        self._logo_cache: dict[str, Image.Image] = {}

    # ------------------------------------------------------------------
    # 图片水印
    # ------------------------------------------------------------------

    def apply_image_watermark(
        self,
        image_data: bytes,
        watermark_config: dict,
        content_type: str = "image/jpeg",
        *,
        required: bool = False,
    ) -> bytes:
        """对图片叠加水印，返回处理后的图片字节。

        watermark_config 支持两种模式:
          - logo: {"type": "logo", "image_key": "assets/logo.png",
                   "position": "bottom-right", "opacity": 0.5, "scale": 0.15}
          - text: {"type": "text", "content": "xxx", "font_size": 24,
                   "color": "#FFFFFF", "position": "bottom-right"}
        ``required`` 仅由已锁定水印快照的定时发布链路使用。普通素材库的历史
        配置仍允许 Logo 临时不可用时保留原图；但定时任务已经明确承诺水印时，
        必须抛出异常阻止无标识图片继续发布。
        """
        mode = watermark_config.get("type", "logo")
        img = Image.open(io.BytesIO(image_data)).convert("RGBA")

        if mode == "logo":
            img = self._apply_logo_watermark(img, watermark_config, required=required)
        elif mode == "text":
            img = self._apply_text_watermark(img, watermark_config)

        # 转回原始格式
        out = io.BytesIO()
        fmt = "PNG" if "png" in content_type else "JPEG"
        save_kw = {"format": fmt}
        if fmt == "JPEG":
            save_kw["quality"] = 92
        img.convert("RGB").save(out, **save_kw)
        return out.getvalue()

    def _apply_logo_watermark(
        self,
        img: Image.Image,
        config: dict,
        *,
        required: bool = False,
    ) -> Image.Image:
        """叠加 Logo 图片水印，并按调用方要求决定是否允许降级。"""
        from app.services.storage_service import storage_service

        image_key = config.get("image_key", "")
        if not image_key:
            logger.warning("Logo watermark: no image_key configured")
            if required:
                raise ValueError("必需 Logo 水印缺少 image_key")
            return img

        # 从缓存或 MinIO 加载 Logo
        logo = self._logo_cache.get(image_key)
        if logo is None:
            try:
                logo_data = storage_service.download_bytes(image_key)
                logo = Image.open(io.BytesIO(logo_data)).convert("RGBA")
                self._logo_cache[image_key] = logo
            except Exception as exc:
                logger.warning("Failed to load logo image '%s': %s", image_key, exc)
                if required:
                    raise RuntimeError(f"必需 Logo 水印加载失败: {image_key}") from exc
                return img

        # 缩放 Logo 到图片尺寸的 scale 比例
        scale = config.get("scale", 0.15)
        max_logo_w = int(img.width * scale)
        max_logo_h = int(img.height * scale)
        logo.thumbnail((max_logo_w, max_logo_h), Image.LANCZOS)

        # 透明度
        opacity = config.get("opacity", 0.8)
        if opacity < 1.0:
            alpha = logo.split()[3]
            alpha = alpha.point(lambda a: int(a * opacity))
            logo.putalpha(alpha)

        # 计算位置
        position = config.get("position", "bottom-right")
        margin = config.get("margin", 20)
        x, y = self._calc_position(img, logo, position, margin)

        # 叠加
        img.paste(logo, (x, y), logo)
        return img

    def _apply_text_watermark(
        self,
        img: Image.Image,
        config: dict,
    ) -> Image.Image:
        """叠加文字水印"""
        content = config.get("content", "")
        if not content:
            return img

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 字体
        font_size = config.get("font_size", 36)
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (IOError, OSError):
            font = ImageFont.load_default()

        # 颜色 + 透明度
        color = config.get("color", "#FFFFFF")
        opacity = config.get("opacity", 0.6)

        # 解析颜色，支持 hex (#FFFFFF) 和 rgba(r,g,b,a) 两种格式
        r = g = b = 255
        if color.startswith("#"):
            hex_str = color.lstrip("#")
            if len(hex_str) >= 6:
                r = int(hex_str[0:2], 16)
                g = int(hex_str[2:4], 16)
                b = int(hex_str[4:6], 16)
        elif color.startswith("rgba") or color.startswith("rgb"):
            import re as _re
            nums = _re.findall(r'\d+', color)
            if len(nums) >= 3:
                r, g, b = int(nums[0]), int(nums[1]), int(nums[2])

        # 计算文字尺寸
        bbox = draw.textbbox((0, 0), content, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

        position = config.get("position", "bottom-right")
        margin = config.get("margin", 20)
        x, y = self._calc_position_by_size(img.width, img.height, tw, th, position, margin)

        draw.text((x, y), content, font=font, fill=(r, g, b, int(255 * opacity)))

        img = Image.alpha_composite(img, overlay)
        return img

    # ------------------------------------------------------------------
    # 位置计算
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_position(
        canvas: Image.Image,
        layer: Image.Image,
        position: str,
        margin: int,
    ) -> Tuple[int, int]:
        return WatermarkService._calc_position_by_size(
            canvas.width, canvas.height, layer.width, layer.height, position, margin,
        )

    @staticmethod
    def _calc_position_by_size(
        cw: int, ch: int, lw: int, lh: int,
        position: str, margin: int,
    ) -> Tuple[int, int]:
        if position == "top-left":
            return margin, margin
        elif position == "top-right":
            return cw - lw - margin, margin
        elif position == "bottom-left":
            return margin, ch - lh - margin
        elif position == "center":
            return (cw - lw) // 2, (ch - lh) // 2
        else:  # bottom-right (default)
            return cw - lw - margin, ch - lh - margin

    # ------------------------------------------------------------------
    # 视频水印
    # ------------------------------------------------------------------

    def apply_video_watermark(
        self,
        input_path: str,
        output_path: str,
        watermark_config: dict,
    ) -> Optional[str]:
        """用 FFmpeg 为视频叠加水印，返回输出路径。

        需要系统安装 FFmpeg 并可在 PATH 中找到。
        """
        import subprocess

        mode = watermark_config.get("type", "logo")

        if mode == "logo":
            image_key = watermark_config.get("image_key", "")
            if not image_key:
                logger.warning("Video watermark: no image_key")
                return None

            from app.services.storage_service import storage_service
            try:
                logo_data = storage_service.download_bytes(image_key)
                logo_path = f"/tmp/wm_logo_{os.path.basename(image_key)}"
                with open(logo_path, "wb") as f:
                    f.write(logo_data)
            except Exception as exc:
                logger.warning("Failed to load watermark logo: %s", exc)
                return None

            position = watermark_config.get("position", "bottom-right")
            ffmpeg_pos = {
                "top-left": "10:10",
                "top-right": "main_w-overlay_w-10:10",
                "bottom-left": "10:main_h-overlay_h-10",
                "bottom-right": "main_w-overlay_w-10:main_h-overlay_h-10",
                "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
            }.get(position, "main_w-overlay_w-10:main_h-overlay_h-10")

            scale_pct = watermark_config.get("scale", 0.15)
            scale_str = f"iw*{scale_pct}:ih*{scale_pct}"

            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-i", logo_path,
                "-filter_complex",
                f"[1:v]scale={scale_str}[logo];[0:v][logo]overlay={ffmpeg_pos}",
                "-c:a", "copy",
                output_path,
            ]
        elif mode == "text":
            content = watermark_config.get("content", "")
            if not content:
                return None
            pos_map = {
                "top-left": "x=(w-text_w)/20:y=(h-text_h)/20",
                "top-right": "x=w-tw-20:y=20",
                "bottom-left": "x=20:y=h-th-20",
                "bottom-right": "x=w-tw-20:y=h-th-20",
                "center": "x=(w-text_w)/2:y=(h-text_h)/2",
            }
            pos = pos_map.get(watermark_config.get("position", "bottom-right"))
            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-vf", f"drawtext=text='{content}':{pos}:fontsize=36:fontcolor=white@0.6",
                "-c:a", "copy",
                output_path,
            ]
        else:
            return None

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error("FFmpeg watermark failed: %s", result.stderr[:500])
                return None
            logger.info("Video watermark applied: %s", output_path)
            return output_path
        except FileNotFoundError:
            logger.error("FFmpeg not found — install ffmpeg and add to PATH for video watermarking")
            return None
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out processing video watermark")
            return None


# 单例
watermark_service = WatermarkService()
