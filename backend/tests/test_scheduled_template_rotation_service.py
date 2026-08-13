"""定时任务来源模板轮换规则的纯逻辑回归测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证纯函数，不连接业务数据库。"""

    yield


def test_disabled_rotation_preserves_single_template_path() -> None:
    """关闭轮换时不应产生配置，调用方继续使用任务原有模板。"""

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
    )

    assert normalize_template_rotation_config(None) is None
    assert normalize_template_rotation_config({"enabled": False}) is None


def test_publish_day_rotation_changes_template_after_each_day() -> None:
    """按发布日且每个模板使用一次时，每个新的发布日都切到下一个模板。"""

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
        select_rotation_profile_id,
    )

    config = normalize_template_rotation_config(
        {
            "enabled": True,
            "profile_ids": [101, 202, 303],
            "basis": "publish_day",
            "uses_per_template": 1,
        }
    )

    assert config is not None
    assert [select_rotation_profile_id(config, index) for index in range(5)] == [
        101,
        202,
        303,
        101,
        202,
    ]


def test_rotation_holds_template_for_configured_occurrences() -> None:
    """每个模板连续使用 N 个发布单位后才切换，适用于按天和按次两种依据。"""

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
        select_rotation_profile_id,
    )

    config = normalize_template_rotation_config(
        {
            "enabled": True,
            "profile_ids": [11, 22],
            "basis": "publish_run",
            "uses_per_template": 2,
        }
    )

    assert config is not None
    assert [select_rotation_profile_id(config, index) for index in range(6)] == [
        11,
        11,
        22,
        22,
        11,
        11,
    ]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"enabled": True, "profile_ids": [1]}, "至少选择 2 个"),
        ({"enabled": True, "profile_ids": [1, 1]}, "不能重复"),
        ({"enabled": True, "profile_ids": [1, 2], "basis": "unknown"}, "轮换依据"),
        ({"enabled": True, "profile_ids": [1, 2], "uses_per_template": 0}, "连续使用次数"),
    ],
)
def test_rotation_rejects_invalid_configuration(payload: dict, message: str) -> None:
    """无效配置应在保存任务前失败，不能留给 Worker 在运行时猜测。"""

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
    )

    with pytest.raises(ValueError, match=message):
        normalize_template_rotation_config(payload)
