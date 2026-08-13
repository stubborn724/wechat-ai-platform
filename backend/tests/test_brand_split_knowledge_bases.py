"""品牌文章格式与视觉背景知识库重建脚本测试。

该脚本的内容是定时任务的业务配置源。测试不连接数据库或调用嵌入模型，
只锁定八份资料的数量、名称与章节边界，避免格式规则重新混入图片背景资料。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """品牌规范定义是纯配置，不依赖本地业务数据库。"""

    yield


def test_brand_split_knowledge_defines_eight_separated_documents() -> None:
    """四个品牌必须各自拥有格式库和背景库，且内容职责不能交叉。"""

    from scripts.rebuild_brand_split_knowledge_bases import BRAND_SPLIT_KNOWLEDGE

    assert len(BRAND_SPLIT_KNOWLEDGE) == 4
    names = []
    for profile in BRAND_SPLIT_KNOWLEDGE:
        names.extend((profile.format_knowledge_base_name, profile.visual_knowledge_base_name))
        assert "【文章形式】" in profile.format_document_text
        assert "【文案要求】" in profile.format_document_text
        assert "【末尾联系方式】" in profile.format_document_text
        assert "【图片要求】" not in profile.format_document_text
        assert "【品牌调性】" in profile.visual_document_text
        assert "【背景要求】" in profile.visual_document_text
        assert "【图片要求】" in profile.visual_document_text
        assert "【文章形式】" not in profile.visual_document_text
        assert "【末尾联系方式】" not in profile.visual_document_text

    assert len(set(names)) == 8


def test_brand_split_knowledge_uses_erp_source_as_task_binding_key() -> None:
    """任务绑定必须以 ERP 来源键匹配，不能依赖易变的定时任务数据库 ID。"""

    from scripts.rebuild_brand_split_knowledge_bases import BRAND_SPLIT_KNOWLEDGE

    assert {profile.erp_source_key for profile in BRAND_SPLIT_KNOWLEDGE} == {
        "xiuman", "zhongxiwujie", "xiehuai", "jianzhi",
    }


def test_xiuman_visual_rule_keeps_contact_details_out_of_image_model() -> None:
    """绣蔓的电话和二维码只能由程序页脚渲染，不能再交给图片模型。"""

    from scripts.rebuild_brand_split_knowledge_bases import BRAND_SPLIT_KNOWLEDGE

    xiuman = next(
        profile for profile in BRAND_SPLIT_KNOWLEDGE
        if profile.erp_source_key == "xiuman"
    )

    assert "每篇图片由程序按不同机位轮换" in xiuman.visual_document_text
    assert "模型不得生成任何可读文字" in xiuman.visual_document_text
    assert "右下角添加艺术字水印" not in xiuman.visual_document_text
    assert "绣蔓家具TEL" not in xiuman.visual_document_text
