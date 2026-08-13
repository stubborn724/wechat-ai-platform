"""三个新增公众号的通用海报任务配置契约。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """配置契约是纯内存测试，不触发项目级业务表清理。"""

    yield


def test_new_brand_poster_configuration_is_three_images_and_brand_scoped():
    """新增公众号复用同一海报模板，并把三天防重范围锁定到品牌。"""

    from app.services.brand_poster_task_configuration import (
        NEW_BRAND_POSTER_CONFIGS,
        build_three_image_template_payload,
    )

    assert set(NEW_BRAND_POSTER_CONFIGS) == {"zhongxiwujie", "xiehuai", "jianzhi"}
    assert build_three_image_template_payload() == {
        "poster_count": 2,
        "seamless": True,
        "total_poster_count": 3,
        "poster_text_overlay_mode": "programmatic_text_v1",
    }
    for source_key, config in NEW_BRAND_POSTER_CONFIGS.items():
        assert config.selection_scope == f"brand:{source_key}"
        assert config.writing_style_template_id
        assert config.poster_template_total_count == 3
        assert config.private_publish_times == ("08:00", "20:00")
        assert config.public_publish_times == ("13:00",)


def test_brand_initialization_never_targets_xiuman_tasks():
    """初始化定义只允许新增三个品牌，防止误调用全量脚本污染正式任务。"""

    from app.services.brand_poster_task_configuration import NEW_BRAND_POSTER_CONFIGS

    assert "xiuman" not in NEW_BRAND_POSTER_CONFIGS
