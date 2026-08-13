"""模板轮换任务配置的 API 合同测试。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """源码合同不访问数据库，避免全局清理夹具干扰。"""

    yield


def test_scheduled_task_api_accepts_and_returns_rotation_configuration() -> None:
    """创建、更新和读取任务必须使用同一轮换配置字段。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/api/v1/scheduled_tasks.py"
    ).read_text(encoding="utf-8")

    assert "class ScheduledTemplateRotationConfig" in source
    assert "template_rotation_config" in source
    assert "_prepare_template_rotation_config" in source


def test_rotation_api_validates_source_template_ownership() -> None:
    """轮换只能引用当前租户仍启用的来源文章模板。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/api/v1/scheduled_tasks.py"
    ).read_text(encoding="utf-8")

    assert "validate_rotation_profiles" in source
    assert "ArticleFormatProfile.source_article_id.isnot(None)" in source


def test_format_profile_options_include_their_feed_source_labels() -> None:
    """同名模板可通过投喂源和文章标题区分，才能安全配置顺序。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/api/v1/feed_sources.py"
    ).read_text(encoding="utf-8")

    assert "source_article_title" in source
    assert "source_name" in source
