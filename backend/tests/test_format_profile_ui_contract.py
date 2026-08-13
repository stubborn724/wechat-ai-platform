"""格式模板前端入口的源码契约。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """源码契约不访问数据库，覆盖全局业务表清理夹具。"""

    yield


def test_feed_source_import_explains_automatic_format_analysis() -> None:
    """投喂源页面应明确说明导入链接后会自动完成格式分析。"""

    source = (Path(__file__).resolve().parents[2] / "frontend/src/views/FeedSourcesView.vue").read_text(encoding="utf-8")

    assert "analyzeArticleFormat" in source
    assert "/format-profiles" in source
    assert "导入后自动分析格式" in source
    assert "重新分析格式" in source
    assert "格式已分析" in source


def test_scheduled_task_form_uses_automatic_profile_binding_by_default() -> None:
    """任务默认随投喂源自动绑定模板，同时保留手动覆盖入口。"""

    source = (Path(__file__).resolve().parents[2] / "frontend/src/views/ScheduledTasksView.vue").read_text(encoding="utf-8")

    assert "format_profile_id" in source
    assert "formatProfiles" in source
    assert "格式模板覆盖（可选）" in source
    assert "自动绑定投喂文章的最新格式模板" in source
    assert "form.format_profile_id = null" in source


def test_feed_source_api_does_not_expose_standalone_format_profile_save_as_contract() -> None:
    """回滚常规模板后，后端不应继续暴露另存接口。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "backend/app/api/v1/feed_sources.py"
    ).read_text(encoding="utf-8")

    assert '"/format-profiles/{profile_id}/save-as"' not in source
    assert "clone_format_profile_as_standalone" not in source


def test_feed_source_ui_does_not_offer_standalone_template_save() -> None:
    """回滚常规模板后，投喂文章页面不应显示另存入口。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/views/FeedSourcesView.vue"
    ).read_text(encoding="utf-8")

    assert "另存为常规模板" not in source
    assert "/format-profiles/${article.format_profile_id}/save-as" not in source
    assert "保存为常规模板" not in source


def test_scheduled_task_format_selector_only_mentions_source_templates() -> None:
    """回滚常规模板后，定时任务选择器只展示来源模板。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/views/ScheduledTasksView.vue"
    ).read_text(encoding="utf-8")

    assert "来源模板" in source
    assert "常规模板" not in source
