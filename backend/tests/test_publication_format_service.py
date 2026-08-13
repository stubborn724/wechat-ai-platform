"""知识库发布格式配置的回归测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """发布格式服务是纯函数，不应被本地业务库的清理状态影响。"""

    yield


def test_poster_profile_keeps_complete_contact_directive_without_truncation():
    """纯海报规范必须完整保留固定联系方式与二维码，不能随品牌正文截断。"""
    from app.services.publication_format_service import analyze_publication_format

    source = """【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。结构为：标题海报图→2~3张竖版长图意境海报→末尾联系方式海报。
【文案要求】每张长图内嵌文案控制在50字左右。主标题不超过12字。
【图片要求】竖版长海报比例，画面朦胧柔和。禁止内嵌二维码，以及不能设计品牌logo。
【末尾联系方式】文章最后固定显示联系方式文案“中西无界TEL: 18138381749”，并附上企业微信二维码图片：https://cdn.example.com/contact.png ，不额外补充任何文案和不修改图片。"""

    profile = analyze_publication_format(source)

    assert profile.is_poster_gallery is True
    assert profile.poster_count == 3
    assert profile.title_max_chars == 12
    assert profile.footer_template == "中西无界TEL: 18138381749\n![二维码](https://cdn.example.com/contact.png)"
    assert "禁止内嵌二维码" in profile.image_directives
    assert "末尾联系方式" in profile.raw_directives


def test_poster_profile_combines_brand_tone_with_image_directives() -> None:
    """海报图片提示词只应拿到品牌视觉和图片规则，不携带文章结构。"""

    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【文案要求】每张长图内嵌文案控制在50字左右。
【品牌调性】中西融合、贵气内敛、文化厚重。
【图片要求】背景采用墨绿、古铜金与低饱和高端家居场景。""")

    assert "中西融合" in profile.visual_directives
    assert "墨绿" in profile.visual_directives
    assert "文章形式" not in profile.visual_directives
    assert "文案要求" not in profile.visual_directives


def test_poster_profile_uses_structured_consultation_card_from_contact_rule() -> None:
    """知识库中的咨询卡规则必须原样成为任务运行时的固定页脚。"""
    from app.services.publication_format_service import analyze_publication_format

    source = """【文章形式】纯海报拼接形式，无独立文字段落。
【末尾联系方式】文章末尾展示产品咨询卡。
【咨询卡】{"type":"consultation_card_v1","brand":"写怀","headline":"产品咨询","phone":"18928694592","qrcodes":[{"label":"企业微信","url":"https://cdn.example.com/qr.png"}]}"""

    profile = analyze_publication_format(source)

    assert '"type":"consultation_card_v1"' in profile.footer_template
    assert '"phone":"18928694592"' in profile.footer_template


def test_poster_html_contains_only_generated_images_before_fixed_footer():
    """纯海报正文不得生成独立文字节点，联系方式只通过固定页脚追加。"""
    from app.services.publication_format_service import render_poster_gallery_html

    content = render_poster_gallery_html(
        image_urls=["https://cdn.example.com/title.png", "https://cdn.example.com/story.png"],
        footer_template="中西无界TEL: 18138381749\n![二维码](https://cdn.example.com/contact.png)",
    )

    before_footer = content.split('data-ai-footer-template="appended"')[0]
    assert "<p" not in before_footer
    assert content.count("<img") == 3
    assert "中西无界TEL: 18138381749" in content


def test_poster_html_images_are_block_level_with_zero_margin() -> None:
    """连续海报切片必须没有底部间距，避免公众号中出现白缝。"""

    from app.services.publication_format_service import render_poster_gallery_html

    content = render_poster_gallery_html(
        image_urls=["https://cdn.example.com/title.png", "https://cdn.example.com/story.png"],
        footer_template="",
    )

    assert content.count('display:block;margin:0;') == 2
    assert "margin:0 auto 12px" not in content
    assert '<div data-ai-layout="seamless-poster"' in content
    assert 'style="margin:0;padding:0;line-height:0;font-size:0;"' in content


def test_poster_html_can_keep_copy_metadata_for_programmatic_overlay() -> None:
    """程序叠字只保存图片属性，不在正文生成可见独立文字节点。"""
    from app.services.publication_format_service import render_poster_gallery_html

    content = render_poster_gallery_html(
        image_urls=["https://cdn.example.com/title.png", "https://cdn.example.com/story.png"],
        footer_template="",
        poster_copies=["一席成景", "让日常回到从容。"],
        programmatic_text_overlay=True,
    )

    assert 'data-poster-copy="一席成景"' in content
    assert 'data-poster-kind="title"' in content
    assert 'data-poster-kind="content"' in content
    assert "<p" not in content


def test_body_copy_only_poster_marks_every_image_as_content() -> None:
    """正文型三图模板不得把第一张重新标记为标题海报。"""
    from app.services.publication_format_service import render_poster_gallery_html

    content = render_poster_gallery_html(
        image_urls=[
            "https://cdn.example.com/one.png",
            "https://cdn.example.com/two.png",
            "https://cdn.example.com/three.png",
        ],
        footer_template="",
        poster_copies=[
            "第一段正文内容，带有完整的阅读节奏。",
            "第二段正文内容，继续展开产品体验。",
            "第三段正文内容，落回日常使用场景。",
        ],
        programmatic_text_overlay=True,
        body_copy_only=True,
    )

    assert content.count('data-poster-kind="content"') == 3
    assert 'data-poster-kind="title"' not in content
