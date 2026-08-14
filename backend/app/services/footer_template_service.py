"""文章固定底部内容的安全 HTML 渲染服务。

前端以简单文本和 ``![二维码](URL)`` 保存固定内容，而 HTML 仿写文章不能直接
拼接 Markdown，否则公众号会把它当作普通文字。该模块集中完成文本转义、二维码
提取和样式生成，供 HTML 仿写与结构化文章共同复用。
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse


_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def build_consultation_card_template(
    *,
    brand: str,
    phone: str,
    qrcodes: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    headline: str = "产品咨询",
) -> str:
    """构造可持久化的咨询卡模板 JSON，供知识库和定时任务共享。

    这里仅保存发布所需的公开展示信息，不读取账号凭证或本地文件。统一协议避免
    各品牌再次手写不同的 Markdown 格式，也让后续增加二维码无需改渲染器。
    """

    return json.dumps(
        {
            "type": "consultation_card_v1",
            "brand": str(brand or "").strip(),
            "headline": str(headline or "产品咨询").strip() or "产品咨询",
            "phone": str(phone or "").strip(),
            "qrcodes": [
                {"label": str(label or "二维码").strip() or "二维码", "url": str(url or "").strip()}
                for label, url in qrcodes
                if str(url or "").strip()
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def render_footer_template_html(template: str) -> str:
    """把固定底部文本和二维码 Markdown 转换为可发布的安全 HTML。

    用户文本始终进行 HTML 转义，避免固定内容破坏投喂源 DOM。图片只接受 HTTP、
    HTTPS 或站内绝对路径；不支持的协议会被忽略，防止把脚本协议写入公众号正文。
    """

    normalized_template = str(template or "").strip()
    if not normalized_template:
        return ""

    consultation_card_html = _render_consultation_card_html(normalized_template)
    if consultation_card_html is not None:
        return consultation_card_html

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


def _render_consultation_card_html(template: str) -> str | None:
    """渲染结构化咨询卡，历史 Markdown 页脚继续走原有兼容分支。

    咨询卡使用表格而非复杂 CSS 布局，微信编辑器对表格和内联样式的保留更稳定。
    二维码地址仍会经过协议校验，不能让任务配置把不安全 URL 带进已发布文章。
    """

    try:
        payload = json.loads(template)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "consultation_card_v1":
        return None

    brand = html.escape(str(payload.get("brand") or "").strip())
    headline = html.escape(str(payload.get("headline") or "产品咨询").strip())
    phone = html.escape(str(payload.get("phone") or "").strip())
    raw_codes = payload.get("qrcodes")
    codes = raw_codes if isinstance(raw_codes, list) else []
    safe_codes = [
        {
            "label": html.escape(str(item.get("label") or "二维码").strip()),
            "url": html.escape(str(item.get("url") or "").strip(), quote=True),
        }
        for item in codes
        if isinstance(item, dict) and _is_allowed_image_url(str(item.get("url") or "").strip())
    ]
    # 二维码可以在品牌上线后补充，但咨询电话是立即可用的转化入口。两者都没有时
    # 才视为没有可展示内容；不能因二维码缺失而把电话一起隐藏。
    if not safe_codes and not phone:
        return ""

    # 咨询区采用与剪纸、写怀一致的白底黑字横向结构：二维码作为明确入口，品牌、
    # 合作诉求和电话作为同一组信息，而不是深色营销卡。两个二维码时仍保留右侧
    # 文字区，避免绣蔓的企业微信和抖音入口破坏整体层级。
    has_qrcodes = bool(safe_codes)
    # 单二维码时缩小二维码列并给文字区更多空间；微信会将内联字号按设备缩放，
    # 旧的 32/68 固定分配会让“中西无界 产品顾问”在“顾问”前意外换行。
    code_width = 30 if len(safe_codes) == 1 else 25
    # 未配置二维码时，文字信息区占满整行，避免保留空白二维码列造成版式失衡。
    detail_width = 100 - code_width * len(safe_codes) if has_qrcodes else 100
    code_size = 180 if len(safe_codes) == 1 else 132
    code_cells = "".join(
        '<td style="width:{width}%;padding:0 8px;text-align:center;vertical-align:middle;">'
        '<img alt="{label}" src="{url}" style="width:{size}px;max-width:100%;height:auto;display:block;margin:0 auto 9px;" />'
        '<p style="margin:0;color:#1f1f1f;font-size:13px;line-height:1.5;">{label}</p>'
        '</td>'.format(width=code_width, size=code_size, **item)
        for item in safe_codes
    )
    phone_html = (
        '<p style="margin:15px 0 0;color:#3d3d3d;font-size:14px;line-height:1.65;">'
        f'咨询热线：<a href="tel:{phone}" style="color:#1f1f1f;text-decoration:none;">{phone}</a></p>'
        if phone
        else ""
    )
    qrcode_hint_html = (
        '<div style="height:1px;background:#d9d9d9;margin:22px 0 18px;"></div>'
        '<p style="margin:0;color:#1f1f1f;font-size:20px;font-weight:600;line-height:1.55;text-align:center;">'
        '扫码/长按识别二维码，添加产品顾问</p>'
        if has_qrcodes
        else ""
    )
    return (
        '<div data-ai-footer-card="consultation-card-v1" '
        'style="margin:30px 0 0;padding:0;background:#ffffff;border-radius:0;">'
        '<div style="padding:24px 22px 22px;background:#ffffff;border:1px solid #d9d9d9;border-radius:0;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed;"><tbody><tr>'
        f'{code_cells}'
        f'<td style="width:{detail_width}%;padding:4px 8px 4px:{20 if has_qrcodes else 8}px;vertical-align:middle;">'
        f'<p style="margin:0;color:#1f1f1f;font-size:24px;font-weight:600;line-height:1.35;white-space:nowrap;word-break:keep-all;">{brand} 产品顾问</p>'
        f'<p style="margin:13px 0 0;color:#3d3d3d;font-size:18px;line-height:1.55;">{headline}　商务合作</p>'
        f'{phone_html}'
        '</td></tr></tbody></table>'
        f'{qrcode_hint_html}'
        '</div></div>'
    )


def _is_allowed_image_url(image_url: str) -> bool:
    """只允许公众号图片处理中可识别的网络地址或站内绝对路径。"""

    if image_url.startswith("/"):
        return True
    return urlparse(image_url).scheme.lower() in {"http", "https"}
