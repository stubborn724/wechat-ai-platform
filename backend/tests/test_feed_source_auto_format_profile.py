"""投喂源自动格式分析的行为测试。"""

from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件用替身验证抓取分支，不访问业务数据库。"""

    yield


def test_auto_profile_creation_reports_new_template(monkeypatch) -> None:
    """新导入的可解析文章应被统计为自动创建格式模板。"""

    import app.services.feed_service as feed_service
    from app.services.format_profile_persistence_service import (
        FormatProfilePersistenceResult,
    )

    article = SimpleNamespace(id=31, title="可解析文章", body_html="<p>正文</p>")
    profile = SimpleNamespace(id=9, render_mode="html_slots", version=1)

    monkeypatch.setattr(
        "app.services.format_profile_persistence_service.create_or_reuse_format_profile",
        lambda _db, *, article: FormatProfilePersistenceResult(profile=profile, created=True),
    )

    result = feed_service.auto_create_format_profile_for_article(
        db=object(),
        article=article,
    )

    assert result.created is True
    assert result.profile_id == 9
    assert result.error is None


def test_auto_profile_creation_keeps_article_import_when_html_is_invalid(monkeypatch) -> None:
    """格式解析失败只返回警告，不应抛出异常中断整个投喂源抓取。"""

    import app.services.feed_service as feed_service

    article = SimpleNamespace(id=32, title="无 HTML 文章", body_html="")

    def raise_invalid_html(_db, *, article):
        raise ValueError("格式模板需要投喂文章的 HTML 内容")

    monkeypatch.setattr(
        "app.services.format_profile_persistence_service.create_or_reuse_format_profile",
        raise_invalid_html,
    )

    result = feed_service.auto_create_format_profile_for_article(
        db=object(),
        article=article,
    )

    assert result.created is False
    assert result.profile_id is None
    assert result.error == "格式模板需要投喂文章的 HTML 内容"


def test_feed_fetch_response_exposes_auto_format_analysis_summary() -> None:
    """前端必须能区分链接抓取成功与其中某篇文章格式分析失败。"""

    from app.api.v1.feed_sources import FetchResultResponse

    response = FetchResultResponse(
        source_id=1,
        source_name="测试来源",
        articles_fetched=1,
        articles_saved=1,
        format_profiles_created=1,
    )

    assert response.format_profiles_created == 1
    assert response.format_profile_errors == []


def test_feed_source_creation_starts_the_first_import_automatically() -> None:
    """用户仅输入链接创建投喂源后，后端应直接启动抓取和格式分析闭环。"""

    source = (
        Path(__file__).resolve().parents[2] / "backend/app/api/v1/feed_sources.py"
    ).read_text(encoding="utf-8")

    assert "async def create_feed_source" in source
    assert "initial_fetch = await fetch_source" in source
