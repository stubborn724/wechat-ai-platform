"""图片排版合成服务 — 用 Pillow 将文字/Logo/二维码合成到背景图上"""

import io
import logging
import math
import os
import tempfile
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

# 模板定义：不同风格的海报排版参数
POSTER_TEMPLATES = {
    "clean": {
        "name": "简洁",
        "title_color": "#FFFFFF",
        "title_shadow": True,
        "subtitle_color": "#F0F0F0",
        "accent_color": "#FF6B35",
        "cta_bg_color": "#FF6B35",
        "cta_text_color": "#FFFFFF",
        "overlay_opacity": 0.35,
        "title_font_ratio": 0.08,   # 字号占短边比例
        "subtitle_font_ratio": 0.04,
        "point_font_ratio": 0.03,
    },
    "marketing": {
        "name": "营销",
        "title_color": "#FFFFFF",
        "title_shadow": True,
        "subtitle_color": "#FFD700",
        "accent_color": "#E74C3C",
        "cta_bg_color": "#E74C3C",
        "cta_text_color": "#FFFFFF",
        "overlay_opacity": 0.4,
        "title_font_ratio": 0.09,
        "subtitle_font_ratio": 0.04,
        "point_font_ratio": 0.032,
    },
    "knowledge": {
        "name": "知识",
        "title_color": "#2C3E50",
        "title_shadow": False,
        "subtitle_color": "#7F8C8D",
        "accent_color": "#3498DB",
        "cta_bg_color": "#3498DB",
        "cta_text_color": "#FFFFFF",
        "overlay_opacity": 0.15,
        "title_font_ratio": 0.07,
        "subtitle_font_ratio": 0.035,
        "point_font_ratio": 0.028,
    },
}

# 比例到像素映射
ASPECT_RATIOS = {
    "1:1": (1080, 1080),
    "3:4": (1080, 1440),
    "9:16": (1080, 1920),
}


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """获取中文字体，优先使用系统字体"""
    font_names = [
        "msyh.ttc", "msyhbd.ttc",       # Microsoft YaHei
        "NotoSansCJK-Regular.ttc",
        "NotoSansSC-Regular.otf",
        "simhei.ttf",                    # SimHei
        "arial.ttf",
    ]
    if bold:
        font_names = [
            "msyhbd.ttc",
            "NotoSansCJK-Bold.ttc",
            "NotoSansSC-Bold.otf",
            "simhei.ttf",
            "arialbd.ttf",
        ]

    for name in font_names:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_rounded_rect(draw: ImageDraw, xy: Tuple, radius: int, fill: Tuple[int, int, int, int]):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _auto_font_size(text: str, max_width: int, max_height: int,
                    font_func, min_size: int = 20, max_size: int = 120) -> int:
    """自动计算字号，使文字在给定区域内不溢出"""
    for size in range(max_size, min_size - 1, -2):
        font = font_func(size)
        bbox = draw = None
        try:
            dummy_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            if text_w <= max_width and text_h <= max_height:
                return size
        except Exception:
            continue
    return min_size


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """按宽度自动换行"""
    lines = []
    chars = list(text)
    current_line = ""
    for ch in chars:
        test_line = current_line + ch
        try:
            dummy_img = Image.new("RGBA", (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = ch
        except Exception:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines


def _add_text_shadow(draw: ImageDraw, pos: Tuple[int, int], text: str,
                     font: ImageFont.FreeTypeFont, shadow_color=(0, 0, 0, 100),
                     offset: int = 2):
    """添加文字阴影"""
    x, y = pos
    draw.text((x + offset, y + offset), text, font=font, fill=shadow_color)


class ImageLayoutService:
    """图片排版合成服务"""

    def compose_poster(
        self,
        background_bytes: bytes,
        main_title: str,
        sub_title: str = "",
        selling_points: Optional[List[str]] = None,
        cta: str = "",
        disclaimer: str = "",
        logo_image_key: Optional[str] = None,
        qr_code_image_key: Optional[str] = None,
        aspect_ratio: str = "3:4",
        template_name: str = "clean",
        brand_color: Optional[str] = None,
    ) -> bytes:
        """合成海报图片

        Args:
            background_bytes: 背景图 bytes
            main_title: 主标题
            sub_title: 副标题
            selling_points: 卖点列表
            cta: 行动引导语
            disclaimer: 免责声明
            logo_image_key: Logo 在 MinIO 的 storage_key
            qr_code_image_key: 二维码在 MinIO 的 storage_key
            aspect_ratio: 比例 1:1 / 3:4 / 9:16
            template_name: 模板名称
            brand_color: 品牌主色调

        Returns:
            合成后的图片 bytes (PNG)
        """
        selling_points = selling_points or []
        template = POSTER_TEMPLATES.get(template_name, POSTER_TEMPLATES["clean"])
        target_size = ASPECT_RATIOS.get(aspect_ratio, (1080, 1440))

        # 打开并裁剪背景图到目标比例
        bg = Image.open(io.BytesIO(background_bytes)).convert("RGBA")
        bg = self._crop_to_ratio(bg, target_size)
        bg = bg.resize(target_size, Image.LANCZOS)

        # 创建半透明遮罩层（底部渐变）
        overlay = Image.new("RGBA", target_size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_h = int(target_size[1] * 0.55)
        for y in range(overlay_h):
            alpha = int(180 * (1 - y / overlay_h) * template["overlay_opacity"] * 2)
            alpha = max(0, min(180, alpha))
            overlay_draw.line([(0, target_size[1] - y), (target_size[0], target_size[1] - y)],
                             fill=(0, 0, 0, alpha))
        bg = Image.alpha_composite(bg, overlay)

        draw = ImageDraw.Draw(bg)
        img_w, img_h = target_size
        margin = int(img_w * 0.06)
        usable_w = img_w - margin * 2

        # ========== 主标题 ==========
        title_font_size = int(min(img_w, img_h) * template["title_font_ratio"])
        title_font_size = max(36, min(96, title_font_size))
        title_font = _get_font(title_font_size, bold=True)

        # 自动缩小溢出标题
        title_lines = _wrap_text(main_title, title_font, usable_w)
        title_y = img_h - int(img_h * 0.48)

        for line in title_lines:
            line = line.strip()
            if not line:
                continue
            title_font_used = title_font
            bbox = draw.textbbox((0, 0), line, font=title_font_used)
            line_w = bbox[2] - bbox[0]
            x = margin if line_w > usable_w else margin + (usable_w - line_w) // 2

            if template["title_shadow"]:
                _add_text_shadow(draw, (x + 2, title_y + 2), line, title_font_used)
            draw.text((x, title_y), line, font=title_font_used, fill=self._parse_color(template["title_color"]))
            title_y += bbox[3] - bbox[1] + 8

        # ========== 副标题 ==========
        if sub_title:
            sub_font_size = int(min(img_w, img_h) * template["subtitle_font_ratio"])
            sub_font_size = max(20, min(48, sub_font_size))
            sub_font = _get_font(sub_font_size)
            bbox = draw.textbbox((0, 0), sub_title, font=sub_font)
            sub_w = bbox[2] - bbox[0]
            sub_x = margin + (usable_w - sub_w) // 2 if sub_w < usable_w else margin
            title_y += 12
            draw.text((sub_x, title_y), sub_title, font=sub_font,
                      fill=self._parse_color(template["subtitle_color"]))

        # ========== 卖点列表 ==========
        if selling_points:
            point_font_size = int(min(img_w, img_h) * template["point_font_ratio"])
            point_font_size = max(18, min(40, point_font_size))
            point_font = _get_font(point_font_size)
            accent_color = brand_color or template["accent_color"]

            title_y += bbox[3] - bbox[1] + 20 if sub_title else 24
            for point in selling_points:
                text = f"• {point}"
                bbox = draw.textbbox((0, 0), text, font=point_font)
                line_h = bbox[3] - bbox[1]
                draw.text((margin + 12, title_y), text, font=point_font,
                          fill=self._parse_color(template["title_color"]))
                title_y += line_h + 6

        # ========== 行动引导语（CTA 按钮） ==========
        if cta:
            cta_font_size = max(20, min(48, int(min(img_w, img_h) * 0.045)))
            cta_font = _get_font(cta_font_size, bold=True)
            bbox = draw.textbbox((0, 0), cta, font=cta_font)
            cta_w = bbox[2] - bbox[0] + 40
            cta_h = bbox[3] - bbox[1] + 20
            cta_x = margin + (usable_w - cta_w) // 2
            cta_y = img_h - int(img_h * 0.18) if disclaimer else img_h - int(img_h * 0.12)

            btn_color = self._parse_color(template["cta_bg_color"])
            _draw_rounded_rect(draw, (cta_x, cta_y, cta_x + cta_w, cta_y + cta_h),
                              radius=cta_h // 2, fill=btn_color)
            text_x = cta_x + (cta_w - (bbox[2] - bbox[0])) // 2
            text_y = cta_y + (cta_h - (bbox[3] - bbox[1])) // 2
            draw.text((text_x, text_y), cta, font=cta_font,
                      fill=self._parse_color(template["cta_text_color"]))

        # ========== 免责声明 ==========
        if disclaimer:
            disc_font = _get_font(14)
            bbox = draw.textbbox((0, 0), disclaimer, font=disc_font)
            disc_w = bbox[2] - bbox[0]
            disc_x = margin + (usable_w - disc_w) // 2
            disc_y = img_h - margin - 10
            draw.text((disc_x, disc_y), disclaimer, font=disc_font, fill=(200, 200, 200, 180))

        # ========== Logo（右下角） ==========
        if logo_image_key:
            try:
                logo_bytes = storage_service.download_bytes(logo_image_key)
                logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
                logo_h = int(img_h * 0.08)
                logo_w = int(logo.width * logo_h / logo.height)
                logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
                logo_x = img_w - logo_w - margin
                logo_y = img_h - int(img_h * 0.35)
                bg.paste(logo, (logo_x, logo_y), logo)
            except Exception as exc:
                logger.warning("Logo overlay failed: %s", exc)

        # ========== 二维码（左下角，叠加在底部更下方） ==========
        if qr_code_image_key:
            try:
                qr_bytes = storage_service.download_bytes(qr_code_image_key)
                qr_img = Image.open(io.BytesIO(qr_bytes)).convert("RGBA")
                qr_size = int(img_w * 0.15)
                qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
                qr_x = margin
                qr_y = img_h - qr_size - margin
                bg.paste(qr_img, (qr_x, qr_y), qr_img)
            except Exception as exc:
                logger.warning("QR code overlay failed: %s", exc)

        # 输出
        output = io.BytesIO()
        bg.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def _crop_to_ratio(self, img: Image.Image, target: Tuple[int, int]) -> Image.Image:
        """居中裁剪图片到目标比例"""
        tw, th = target
        iw, ih = img.size
        target_ratio = tw / th
        img_ratio = iw / ih

        if img_ratio > target_ratio:
            # 图片太宽，裁剪左右
            new_iw = int(ih * target_ratio)
            offset = (iw - new_iw) // 2
            return img.crop((offset, 0, offset + new_iw, ih))
        else:
            # 图片太高，裁剪上下
            new_ih = int(iw / target_ratio)
            offset = (ih - new_ih) // 2
            return img.crop((0, offset, iw, offset + new_ih))

    @staticmethod
    def _parse_color(hex_color: str) -> Tuple[int, int, int, int]:
        """解析 HEX 颜色为 RGBA 元组"""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return (r, g, b, 255)
        return (255, 255, 255, 255)


image_layout_service = ImageLayoutService()
