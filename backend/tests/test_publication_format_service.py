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
