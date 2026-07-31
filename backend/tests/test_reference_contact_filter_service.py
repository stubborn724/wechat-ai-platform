"""投喂源联系方式过滤的回归测试。

联系方式属于来源账号的运营信息，不能作为仿写正文结构或参考上下文的一部分；
否则即使二维码在最终阶段被替换，Agent 仍可能重新生成来源账号的联系卡。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只测试纯内容转换，不连接业务数据库。"""
    yield


def test_remove_contact_section_from_layout_template_and_recalculate_totals():
    """布局模板中的联系区必须整体移除，且统计值要反映剩余正文。"""
    from app.schemas.article import LayoutBlock, LayoutSection, LayoutTemplate
    from app.services.reference_contact_filter_service import remove_contact_sections_from_layout_template

    template = LayoutTemplate(
        sections=[
            LayoutSection(
                section_role="opening",
                blocks=[LayoutBlock(type="paragraph", count=1)],
            ),
            LayoutSection(
                section_role="selling_point",
                blocks=[LayoutBlock(type="image", count=2)],
            ),
            LayoutSection(
                section_role="contact_section",
                blocks=[
                    LayoutBlock(type="heading", count=1),
                    LayoutBlock(type="paragraph", count=2),
                    LayoutBlock(type="image", count=1),
                ],
            ),
        ],
        total_paragraph_count=3,
        total_image_count=3,
        layout_features=["double_images_after_sections", "contact_qrcode_footer"],
    )

    filtered = remove_contact_sections_from_layout_template(template)

    assert [section.section_role for section in filtered.sections] == ["opening", "selling_point"]
    assert filtered.total_paragraph_count == 1
    assert filtered.total_image_count == 2
    assert filtered.layout_features == ["double_images_after_sections"]


def test_strip_trailing_contact_markdown_before_it_reaches_content_agent():
    """投喂源末尾的购买提示、参考电话和二维码不能进入文本仿写上下文。"""
    from app.services.reference_contact_filter_service import strip_reference_contact_markdown

    reference_markdown = """# 参考标题

这是应当保留的正文段落。

## 联系我们

试坐优先，如需购买请通过产品详情或官方渠道咨询。

参考电话：18682130473

![企业微信二维码](https://example.com/source-qrcode.png)
"""

    cleaned = strip_reference_contact_markdown(reference_markdown)

    assert "这是应当保留的正文段落。" in cleaned
    assert "联系我们" not in cleaned
    assert "试坐优先" not in cleaned
    assert "18682130473" not in cleaned
    assert "source-qrcode.png" not in cleaned


def test_strip_trailing_operational_note_with_purchase_guidance_before_agent_input():
    """来源文章的温馨提示与购买引导同属联系区，必须整体从仿写上下文移除。"""
    from app.services.reference_contact_filter_service import strip_reference_contact_markdown

    reference_markdown = """# 参考标题

这是需要保留的正文内容。

温馨提示

试坐优先，如需购买，请通过产品详情或官方渠道按实报价。

绣蔓家具TEL:18682130473
"""

    cleaned = strip_reference_contact_markdown(reference_markdown)

    assert "这是需要保留的正文内容。" in cleaned
    assert "温馨提示" not in cleaned
    assert "试坐优先" not in cleaned
    assert "产品详情" not in cleaned
    assert "18682130473" not in cleaned


def test_scheduled_template_loader_removes_contact_section_before_agent_uses_it():
    """定时任务加载历史分析结果时也必须过滤联系章节。"""
    from types import SimpleNamespace

    from app.tasks.scheduled_task_executor import _load_layout_template

    state = SimpleNamespace(layout_template=None)
    feed_article = SimpleNamespace(
        analysis={
            "layout_status": "completed",
            "layout_template": {
                "sections": [
                    {
                        "section_role": "opening",
                        "blocks": [{"type": "paragraph", "count": 1}],
                    },
                    {
                        "section_role": "contact_section",
                        "blocks": [{"type": "image", "count": 1}],
                    },
                ],
                "total_paragraph_count": 1,
                "total_image_count": 1,
            },
        }
    )

    _load_layout_template(state, feed_article)

    assert [section.section_role for section in state.layout_template.sections] == ["opening"]
    assert state.layout_template.total_image_count == 0


def test_scheduled_reference_context_excludes_trailing_contact_markdown():
    """定时任务传给 Agent 的参考正文不得包含来源账号的联系区。"""
    from app.tasks.scheduled_task_executor import _build_reference_article_for_imitation

    context = _build_reference_article_for_imitation(
        "参考标题",
        "正文保留。\n\n## 联系我们\n\n参考电话：18682130473\n\n![二维码](https://example.com/qrcode.png)",
    )

    assert "正文保留。" in context
    assert "联系我们" not in context
    assert "18682130473" not in context
    assert "qrcode.png" not in context
