"""写作模板目录的可复用性与接口边界测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """该模块只验证纯函数和响应模型，不连接或清理业务数据库。"""

    yield


def test_shege_template_exposes_operator_facing_metadata_and_prompt() -> None:
    """她格模板应同时提供页面说明与生成链路所需的内部规则。"""

    from app.services.writing_style_template_service import get_writing_style_template

    template = get_writing_style_template("shege_enterprise_ai_service")

    assert template is not None
    assert template.label == "她格 - 企业 AI 服务"
    assert "经营问题" in template.description
    assert "标题" in template.prompt


def test_writing_style_template_options_hide_internal_prompt() -> None:
    """运营页面只需要名称和用途说明，不能展示底层提示词。"""

    from app.api.v1.scheduled_tasks import build_writing_style_template_options

    options = build_writing_style_template_options()

    shege_option = next(
        option
        for option in options
        if option.identifier == "shege_enterprise_ai_service"
    )
    assert shege_option.label == "她格 - 企业 AI 服务"
    assert shege_option.description
    assert "prompt" not in shege_option.model_dump()


def test_four_original_brand_templates_define_distinct_title_rules() -> None:
    """四个原创品牌必须使用独立标题模板，不能回退到通用产品标签标题。"""

    from app.services.writing_style_template_service import (
        get_writing_style_template,
        get_writing_style_template_title_max_chars,
    )

    expected_templates = {
        "zhongxiwujie_east_west_living": "中西无界 - 东方奢雅生活",
        "xiehuai_oriental_living": "写怀 - 东方留白生活",
        "jianzhi_artful_living": "剪纸系列 - 当代艺术生活",
        "shege_enterprise_ai_service": "她格 - 企业 AI 服务",
    }
    for identifier, label in expected_templates.items():
        template = get_writing_style_template(identifier)

        assert template is not None
        assert template.label == label
        assert "标题" in template.prompt

    assert get_writing_style_template_title_max_chars(
        "xiehuai_oriental_living"
    ) == 26
    assert get_writing_style_template_title_max_chars(
        "shege_enterprise_ai_service"
    ) is None


def test_four_original_brand_templates_require_viewpoint_led_long_titles() -> None:
    """原创标题应是带业务或审美观点的完整长句，不能回到标签式短标题。"""

    from app.services.writing_style_template_service import (
        get_writing_style_template_prompt,
    )

    identifiers = (
        "zhongxiwujie_east_west_living",
        "xiehuai_oriental_living",
        "jianzhi_artful_living",
        "shege_enterprise_ai_service",
    )
    for identifier in identifiers:
        prompt = get_writing_style_template_prompt(identifier)

        assert "完整长句" in prompt
        assert "型号" in prompt
        assert "观点" in prompt
