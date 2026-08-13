"""定时任务自动绑定格式模板的选择规则测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证候选模板的确定性选择规则。"""

    yield


def _candidate(profile_id: int, article_id: int, source_id: int, version: int):
    """构造带投喂来源信息的格式模板候选。"""

    return SimpleNamespace(
        profile=SimpleNamespace(
            id=profile_id,
            source_article_id=article_id,
            version=version,
        ),
        feed_source_id=source_id,
    )


def test_selected_reference_article_has_priority_over_source_latest_profile() -> None:
    """用户选定文章时，不能被同一投喂源更新的其他文章替换版式。"""

    from app.services.format_profile_task_binding_service import select_format_profile_candidate

    selected = _candidate(profile_id=11, article_id=101, source_id=7, version=1)
    newer_from_same_source = _candidate(profile_id=12, article_id=102, source_id=7, version=3)

    resolved = select_format_profile_candidate(
        candidates=[newer_from_same_source, selected],
        feed_article_ids=[101],
        feed_source_id=7,
        feed_source_ids=[7],
    )

    assert resolved is selected.profile


def test_source_latest_profile_is_used_without_selected_article() -> None:
    """只选投喂源时应使用该来源最新文章的最新模板。"""

    from app.services.format_profile_task_binding_service import select_format_profile_candidate

    older = _candidate(profile_id=21, article_id=201, source_id=8, version=1)
    latest = _candidate(profile_id=22, article_id=202, source_id=8, version=2)

    resolved = select_format_profile_candidate(
        candidates=[older, latest],
        feed_article_ids=[],
        feed_source_id=8,
        feed_source_ids=[8],
    )

    assert resolved is latest.profile


def test_no_feed_context_does_not_infer_format_profile() -> None:
    """旧 ERP 任务没有投喂源时必须保持未绑定，不能按名称或知识库猜测。"""

    from app.services.format_profile_task_binding_service import select_format_profile_candidate

    resolved = select_format_profile_candidate(
        candidates=[_candidate(profile_id=31, article_id=301, source_id=9, version=1)],
        feed_article_ids=[],
        feed_source_id=None,
        feed_source_ids=[],
    )

    assert resolved is None


def test_legacy_task_is_not_eligible_for_automatic_profile_binding() -> None:
    """历史任务必须显式迁移为新模式后才允许自动绑定，保护正式绣蔓任务。"""

    from app.services.format_profile_task_binding_service import (
        allows_automatic_format_profile_binding,
    )

    legacy_task = SimpleNamespace(format_profile_auto_bind_enabled=False)
    new_task = SimpleNamespace(format_profile_auto_bind_enabled=True)

    assert allows_automatic_format_profile_binding(legacy_task) is False
    assert allows_automatic_format_profile_binding(new_task) is True
