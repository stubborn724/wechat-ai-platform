"""固定咨询卡页脚的结构化渲染测试。"""

import json

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """咨询卡为纯 HTML 渲染逻辑，不应触发项目级数据库清理。"""

    yield


def test_render_footer_template_renders_product_consultation_card_with_phone_and_two_codes():
    """绣蔓咨询卡必须同时保留企业微信和抖音入口，并显示电话。"""
    from app.services.footer_template_service import render_footer_template_html

    template = json.dumps({
        "type": "consultation_card_v1",
        "brand": "绣蔓家具",
        "headline": "产品咨询",
        "phone": "18682130473",
        "qrcodes": [
            {"label": "企业微信", "url": "https://cdn.example.com/wecom.png"},
            {"label": "抖音号 3746366286", "url": "https://cdn.example.com/douyin.png"},
        ],
    }, ensure_ascii=False)

    html = render_footer_template_html(template)

    assert 'data-ai-footer-card="consultation-card-v1"' in html
    assert "产品咨询" in html
    assert "18682130473" in html
    assert "企业微信" in html
    assert "抖音号 3746366286" in html
    assert "扫码/长按识别二维码" in html
    assert html.count("<img") == 2


def test_build_consultation_card_template_preserves_structured_contact_data():
    """结构化页脚必须可持久化，供知识库规则和定时任务共同复用。"""
    from app.services.footer_template_service import build_consultation_card_template

    template = build_consultation_card_template(
        brand="中西无界",
        phone="18138381749",
        qrcodes=(("企业微信", "https://cdn.example.com/wecom.png"),),
    )

    payload = json.loads(template)
    assert payload["type"] == "consultation_card_v1"
    assert payload["headline"] == "产品咨询"
    assert payload["qrcodes"][0]["label"] == "企业微信"


def test_render_consultation_card_uses_reference_style_horizontal_contact_layout():
    """产品咨询页脚应采用浅色横向成品卡，而不是黑底居中小二维码表单。"""
    from app.services.footer_template_service import render_footer_template_html

    template = json.dumps({
        "type": "consultation_card_v1",
        "brand": "剪纸系列",
        "phone": "18924894639",
        "qrcodes": [
            {"label": "企业微信", "url": "https://cdn.example.com/wecom.png"},
        ],
    }, ensure_ascii=False)

    html = render_footer_template_html(template)

    assert "background:#ffffff" in html
    assert "background:#f4eee6" not in html
    assert "background:#fffdf9" not in html
    assert "background:#171717" not in html
    assert "剪纸系列 产品顾问" in html
    assert "产品咨询" in html
    assert "商务合作" in html
    assert "width:32%" in html
    assert "width:68%" in html
    assert "width:180px" in html


def test_render_consultation_card_keeps_phone_when_qrcodes_are_empty():
    """二维码尚未配置时，咨询页脚仍必须展示品牌、咨询主题和联系电话。"""
    from app.services.footer_template_service import render_footer_template_html

    template = json.dumps({
        "type": "consultation_card_v1",
        "brand": "她格",
        "headline": "企业 AI 转型咨询",
        "phone": "18613093631",
        "qrcodes": [],
    }, ensure_ascii=False)

    html = render_footer_template_html(template)

    assert 'data-ai-footer-card="consultation-card-v1"' in html
    assert "她格 产品顾问" in html
    assert "企业 AI 转型咨询" in html
    assert "18613093631" in html
    assert "扫码/长按识别二维码" not in html
    assert "<img" not in html
