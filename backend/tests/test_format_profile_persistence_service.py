"""投喂文章格式模板持久化服务的回归测试。

格式模板会在链接抓取成功后自动生成，因此版本判断必须是确定性的：同一份 HTML
不能反复产生模板，版式发生变化时才创建新版本。测试刻意不连接业务数据库，避免
将格式判定与环境中的测试数据、外键状态耦合。
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证纯服务逻辑，不访问业务数据库。"""

    yield


def test_same_article_content_reuses_existing_format_profile() -> None:
    """重复抓取同一篇文章时应复用模板，避免版本膨胀和任务选择混乱。"""

    from app.services.format_profile_persistence_service import (
        build_format_profile_snapshot,
        is_same_format_profile_snapshot,
    )

    first = build_format_profile_snapshot(
        article_id=21,
        article_title="格式测试文章",
        body_html="<section><h1>参考标题</h1><p>参考正文</p></section>",
    )
    existing_profile = SimpleNamespace(
        render_mode=first.render_mode,
        template_payload=first.template_payload,
        title_policy=first.title_policy,
    )

    assert is_same_format_profile_snapshot(existing_profile, first) is True


def test_changed_article_content_requires_next_format_profile_version() -> None:
    """源文章从图文变为纯海报时，旧模板不能被覆盖。"""

    from app.services.format_profile_persistence_service import (
        build_format_profile_snapshot,
        is_same_format_profile_snapshot,
        next_format_profile_version,
    )

    html_snapshot = build_format_profile_snapshot(
        article_id=22,
        article_title="格式测试文章",
        body_html="<section><h1>参考标题</h1><p>参考正文</p></section>",
    )
    existing_profile = SimpleNamespace(
        render_mode=html_snapshot.render_mode,
        template_payload=html_snapshot.template_payload,
        title_policy=html_snapshot.title_policy,
        version=1,
    )
    poster_snapshot = build_format_profile_snapshot(
        article_id=22,
        article_title="格式测试文章",
        body_html='<section><img src="https://cdn.example.com/poster-1.jpg" /></section>',
    )

    assert is_same_format_profile_snapshot(existing_profile, poster_snapshot) is False
    assert next_format_profile_version(existing_profile) == 2


def test_empty_html_is_rejected_before_persistence() -> None:
    """没有可解析 HTML 的文章不能伪造模板，调用方可据此保留抓取警告。"""

    from app.services.format_profile_persistence_service import build_format_profile_snapshot

    with pytest.raises(ValueError, match="HTML"):
        build_format_profile_snapshot(
            article_id=23,
            article_title="空文章",
            body_html="",
        )

