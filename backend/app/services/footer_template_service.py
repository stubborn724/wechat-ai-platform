"""文章固定底部内容的安全 HTML 渲染服务。

前端以简单文本和 ``![二维码](URL)`` 保存固定内容，而 HTML 仿写文章不能直接
拼接 Markdown，否则公众号会把它当作普通文字。该模块集中完成文本转义、二维码
提取和样式生成，供 HTML 仿写与结构化文章共同复用。
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse


_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def render_footer_template_html(template: str) -> str:
    """把固定底部文本和二维码 Markdown 转换为可发布的安全 HTML。

    用户文本始终进行 HTML 转义，避免固定内容破坏投喂源 DOM。图片只接受 HTTP、
    HTTPS 或站内绝对路径；不支持的协议会被忽略，防止把脚本协议写入公众号正文。
    """

    normalized_template = str(template or "").strip()
    if not normalized_template:
        return ""

    image_matches = list(_MARKDOWN_IMAGE_PATTERN.finditer(normalized_template))
    text_content = _MARKDOWN_IMAGE_PATTERN.sub("", normalized_template)
    parts: list[str] = []

    for line_index, raw_line in enumerate(text_content.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        safe_line = html.escape(line)
        # 第一行通常是“联系我们”等卡片标题，使用参考卡片内的紧凑标题层级；
        # 后续行保持普通说明文字，避免引入新的嵌套卡片。
        if line_index == 0:
            parts.append(
                '<p style="font-size:15px;font-weight:600;color:#333;margin:0 0 12px;">'
                f"{safe_line}</p>"
            )
        else:
            parts.append(
                '<p style="font-size:14px;line-height:1.8;color:#555;margin:4px 0;">'
                f"{safe_line}</p>"
            )

    for match in image_matches:
        alt_text = html.escape((match.group(1) or "二维码").strip() or "二维码")
        image_url = (match.group(2) or "").strip()
        if not _is_allowed_image_url(image_url):
            continue
        safe_url = html.escape(image_url, quote=True)
        parts.append(
            f'<img alt="{alt_text}" src="{safe_url}" '
            'style="max-width:200px;width:100%;height:auto;border-radius:4px;'
            'display:block;margin:12px 0 0;" />'
        )

    return "".join(parts)


def _is_allowed_image_url(image_url: str) -> bool:
    """只允许公众号图片处理中可识别的网络地址或站内绝对路径。"""

    if image_url.startswith("/"):
        return True
    return urlparse(image_url).scheme.lower() in {"http", "https"}
