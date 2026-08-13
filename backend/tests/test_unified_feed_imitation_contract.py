"""只输入链接即可进入通用仿写闭环的源码契约测试。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """源码契约不访问数据库。"""

    yield


def test_link_import_connects_analysis_binding_and_execution_pipeline() -> None:
    """投喂、任务和执行器必须连接到同一套版本化格式模板能力。"""

    project_root = Path(__file__).resolve().parents[2]
    feed_source_api = (project_root / "backend/app/api/v1/feed_sources.py").read_text(encoding="utf-8")
    feed_service = (project_root / "backend/app/services/feed_service.py").read_text(encoding="utf-8")
    persistence_service = (project_root / "backend/app/services/format_profile_persistence_service.py").read_text(encoding="utf-8")
    task_binding_service = (project_root / "backend/app/services/format_profile_task_binding_service.py").read_text(encoding="utf-8")
    scheduled_api = (project_root / "backend/app/api/v1/scheduled_tasks.py").read_text(encoding="utf-8")
    executor = (project_root / "backend/app/tasks/scheduled_task_executor.py").read_text(encoding="utf-8")

    assert "initial_fetch = await fetch_source" in feed_source_api
    assert "auto_create_format_profile_for_article" in feed_service
    assert "create_or_reuse_format_profile" in persistence_service
    assert "find_automatic_format_profile" in task_binding_service
    assert "_resolve_automatic_format_profile_id" in scheduled_api
    assert "should_use_format_profile" in executor
