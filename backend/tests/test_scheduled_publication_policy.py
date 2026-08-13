"""定时任务发布版式选择策略测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """策略函数只接收值，不应连接业务数据库。"""

    yield


def test_standard_task_cannot_be_promoted_to_poster_by_knowledge_base() -> None:
    """旧任务即使知识库包含海报规则，也必须继续走原有生成链路。"""

    from app.services.scheduled_publication_policy import should_use_poster_layout

    profile = SimpleNamespace(is_poster_gallery=True)

    assert should_use_poster_layout("standard", profile) is False


def test_poster_task_requires_both_explicit_mode_and_poster_profile() -> None:
    """新海报任务必须同时显式选择模式并匹配海报格式规则。"""

    from app.services.scheduled_publication_policy import should_use_poster_layout

    assert should_use_poster_layout(
        "seamless_poster",
        SimpleNamespace(is_poster_gallery=True),
    ) is True
    assert should_use_poster_layout(
        "seamless_poster",
        SimpleNamespace(is_poster_gallery=False),
    ) is False
