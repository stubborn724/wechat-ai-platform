"""固定咨询卡的纯白底黑字视觉回归测试。"""

import json


def test_consultation_card_uses_plain_white_background_and_black_text():
    """绣蔓咨询卡要与剪纸、写怀一致，保持白底黑字而非深色或暖色卡片。"""

    from app.services.footer_template_service import render_footer_template_html

    html = render_footer_template_html(json.dumps({
        "type": "consultation_card_v1",
        "brand": "绣蔓家具",
        "headline": "产品咨询",
        "phone": "18682130473",
        "qrcodes": [
            {"label": "企业微信", "url": "https://cdn.example.com/wecom.png"},
            {"label": "抖音号 3746366286", "url": "https://cdn.example.com/douyin.png"},
        ],
    }, ensure_ascii=False))

    assert "background:#ffffff" in html
    assert "background:#f4eee6" not in html
    assert "background:#171717" not in html
    assert "color:#1f1f1f" in html
