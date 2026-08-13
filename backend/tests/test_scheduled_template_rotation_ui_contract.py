"""定时任务模板轮换页面的源码合同测试。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """源码合同不连接数据库。"""

    yield


def test_scheduled_task_ui_exposes_compact_rotation_controls() -> None:
    """页面只展示一个开关和三项必要配置，不拆成多个互斥模式。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/views/ScheduledTasksView.vue"
    ).read_text(encoding="utf-8")

    assert "template_rotation_config" in source
    assert "rotationProfileIds" in source
    assert "按发布日" in source
    assert "按发布次数" in source
    assert "uses_per_template" in source
    assert "moveRotationProfile" in source


def test_scheduled_task_ui_does_not_send_disabled_rotation_for_existing_tasks() -> None:
    """关闭轮换时保留旧任务请求形态，避免无关编辑触发轮换版本变化。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/views/ScheduledTasksView.vue"
    ).read_text(encoding="utf-8")

    assert "rotationConfigTouched" in source
    assert "payload.template_rotation_config" in source
