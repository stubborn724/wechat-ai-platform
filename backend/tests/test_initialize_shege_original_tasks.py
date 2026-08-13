"""她格原创图文任务的纯配置契约测试。"""

import json

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """配置构造不访问业务数据库，避免测试环境清理实际业务表。"""

    yield


def test_build_shege_task_specs_uses_standard_articles_without_erp_or_poster():
    """她格任务只能走知识库原创图文，不能继承家具、ERP 或海报配置。"""
    from scripts.initialize_shege_original_tasks import build_shege_task_specs

    specifications = build_shege_task_specs(
        knowledge_base_ids=[701, 702],
        account_id=103,
    )

    assert specifications["她格原创-公域"]["publish_times"] == ["13:00"]
    assert specifications["她格原创-私域"]["publish_times"] == ["08:00", "20:00"]
    for specification in specifications.values():
        footer_template = json.loads(specification["footer_template"])

        assert specification["writing_mode"] == "kb"
        assert specification["style"] == "shege_enterprise_ai_service"
        assert specification["knowledge_base_ids"] == [701, 702]
        assert specification["account_ids"] == [103]
        assert specification["publish_mode"] == "direct"
        assert specification["content_type"] == "article"
        assert specification["layout_mode"] == "standard"
        assert specification["image_source"] == "dashscope"
        assert specification["enabled_image_methods"] == ["DASHSCOPE"]
        assert specification["feed_source_id"] is None
        assert specification["feed_source_ids"] is None
        assert specification["format_profile_id"] is None
        assert specification["erp_image_config"] is None
        assert specification["enable_watermark"] is False
        assert footer_template["brand"] == "她格"
        assert footer_template["headline"] == "企业 AI 转型咨询"
        assert footer_template["phone"] == "18613093631"
        assert footer_template["qrcodes"] == []

    assert specifications["她格原创-公域"]["publish_domain"] == "public"
    assert specifications["她格原创-公域"]["public_count"] == 1
    assert specifications["她格原创-私域"]["publish_domain"] == "private"
    assert specifications["她格原创-私域"]["private_count"] == 1
