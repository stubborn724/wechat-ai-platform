"""格式模板服务的行为契约。

格式分析必须由程序完成并持久化，后续定时任务只消费模板摘要，不能每次把原始
HTML 或完整文章再次传给文本模型。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件验证纯格式转换，不访问业务数据库。"""

    yield


def test_html_source_creates_reusable_slot_profile() -> None:
    """普通图文应保存 DOM 槽位和标题约束，而非保存模型生成的内容。"""

    from app.services.format_profile_service import analyze_feed_article_format

    profile = analyze_feed_article_format(
        article_id=21,
        article_title="参考文章",
        body_html=(
            '<section style="color:#333"><h1>参考主标题</h1>'
            '<p>这是需要重新创作的第一段正文。</p>'
            '<img src="https://cdn.example.com/a.jpg" /></section>'
        ),
    )

    assert profile.render_mode == "html_slots"
    assert profile.title_policy["visual_title_slot_id"] == "text-1"
    assert profile.template_payload["blueprint"]["text_slots"][0]["slot_id"] == "text-1"
    assert profile.template_payload["blueprint"]["image_slots"][0]["slot_id"] == "image-1"


def test_image_only_source_creates_seamless_poster_profile() -> None:
    """多图投喂文章应识别为连续海报，并在模板中保存零间距输出规则。"""

    from app.services.format_profile_service import analyze_feed_article_format

    profile = analyze_feed_article_format(
        article_id=22,
        article_title="参考海报",
        body_html=(
            '<section><img src="https://cdn.example.com/1.jpg" />'
            '<img src="https://cdn.example.com/2.jpg" />'
            '<img src="https://cdn.example.com/3.jpg" /></section>'
        ),
    )

    assert profile.render_mode == "poster_gallery"
    assert profile.template_payload["poster_count"] == 3
    assert profile.template_payload["seamless"] is True
    assert profile.title_policy["visual_title_mode"] == "first_poster"


def test_profile_blueprint_round_trip_keeps_original_html_structure() -> None:
    """持久化后重新构建蓝图必须保持原始 DOM，保证 19 图模板不会版式漂移。"""

    from app.services.format_profile_service import (
        analyze_feed_article_format,
        html_blueprint_from_profile_payload,
    )

    profile = analyze_feed_article_format(
        article_id=23,
        article_title="结构模板",
        body_html='<div class="origin"><h1>原始标题</h1><p>原始正文</p><img src="https://cdn.example.com/a.jpg" /></div>',
    )
    blueprint = html_blueprint_from_profile_payload(profile.template_payload)

    assert 'class="origin"' in blueprint.html_template
    assert blueprint.text_slots[0].slot_id == "text-1"
    assert blueprint.image_slots[0].source_url == "https://cdn.example.com/a.jpg"


def test_poster_template_overrides_only_poster_count_of_existing_visual_rules() -> None:
    """海报模板只接管切片数量，品牌视觉和页脚仍可复用现有知识库规则。"""

    from app.services.format_profile_service import apply_poster_template_to_publication_profile
    from app.services.publication_format_service import analyze_publication_format

    original = analyze_publication_format(
        """【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】墨绿与古铜金背景。
【末尾联系方式】固定显示联系方式文案“品牌 TEL: 123”，并附上企业微信二维码图片：https://cdn.example.com/qr.png"""
    )
    updated = apply_poster_template_to_publication_profile(
        original,
        {"poster_count": 6, "seamless": True},
    )

    assert updated.poster_count == 6
    assert updated.visual_directives == original.visual_directives
    assert updated.footer_template == original.footer_template
