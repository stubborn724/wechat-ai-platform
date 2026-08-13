"""轮换模板与定时运行记录绑定的执行器合同测试。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """源码合同不连接数据库。"""

    yield


def test_scheduler_freezes_rotation_template_when_creating_run() -> None:
    """排队时必须写入模板快照，不能等 Worker 执行时再计算。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/tasks/scheduled_task_executor.py"
    ).read_text(encoding="utf-8")

    assert "resolve_rotation_profile_for_scheduled_slot" in source
    assert "format_profile_id=rotation_profile_id" in source
    assert "template_rotation_version=rotation_version" in source


def test_article_executor_reads_run_template_snapshot_for_rotation_tasks() -> None:
    """重试相同 run 时应使用运行记录模板，而不是任务后来修改的模板。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/tasks/scheduled_task_executor.py"
    ).read_text(encoding="utf-8")

    assert "effective_format_profile_id" in source
    assert "getattr(run, \"format_profile_id\", None)" in source
    assert "getattr(run, \"template_rotation_version\", None)" in source
