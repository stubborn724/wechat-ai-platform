"""定时任务与投喂文章格式模板的自动绑定服务。

本模块只负责“根据投喂上下文找哪个模板”，不负责执行渲染。历史任务是否允许自动
绑定由独立的任务开关保护，执行器仍只读取最终持久化的模板外键，因此旧 ERP 任务
即使有投喂源也会保持原有生成路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from app.models.mysql_models import ArticleFormatProfile, FeedSourceArticle


@dataclass(frozen=True)
class FormatProfileCandidate:
    """带来源归属的模板候选，避免选择函数依赖数据库查询细节。"""

    profile: ArticleFormatProfile
    feed_source_id: int


def allows_automatic_format_profile_binding(task: object) -> bool:
    """判断任务是否属于新建的自动格式模式。

    该字段是历史任务与新任务之间的安全边界。迁移脚本会把已有任务设为 False，
    因此即使它们绑定了投喂源，也不会在编辑或再次保存时被悄悄切换版式。
    """

    return bool(getattr(task, "format_profile_auto_bind_enabled", False))


def find_automatic_format_profile(
    db: Session,
    *,
    tenant_id: int,
    feed_article_ids: Sequence[int] | None,
    feed_source_id: int | None,
    feed_source_ids: Sequence[int] | None,
) -> ArticleFormatProfile | None:
    """查询当前投喂上下文的最佳启用模板。

    查询一次取回候选再交给纯选择函数，保证创建和编辑任务遵循完全相同的优先级，
    也便于在不连接数据库的情况下回归测试选择规则。
    """

    article_ids = _normalize_ids(feed_article_ids)
    source_ids = _ordered_source_ids(feed_source_id, feed_source_ids)
    if not article_ids and not source_ids:
        return None

    profile_query = (
        db.query(ArticleFormatProfile, FeedSourceArticle.feed_source_id)
        .join(
            FeedSourceArticle,
            FeedSourceArticle.id == ArticleFormatProfile.source_article_id,
        )
        .filter(
            ArticleFormatProfile.tenant_id == tenant_id,
            ArticleFormatProfile.is_active == True,  # noqa: E712
            FeedSourceArticle.tenant_id == tenant_id,
        )
    )
    if article_ids:
        profile_query = profile_query.filter(FeedSourceArticle.id.in_(article_ids))
    else:
        profile_query = profile_query.filter(
            FeedSourceArticle.feed_source_id.in_(source_ids)
        )

    candidates = [
        FormatProfileCandidate(profile=profile, feed_source_id=source_id)
        for profile, source_id in (
            profile_query.all()
        )
    ]
    return select_format_profile_candidate(
        candidates=candidates,
        feed_article_ids=article_ids,
        feed_source_id=feed_source_id,
        feed_source_ids=source_ids,
    )


def select_format_profile_candidate(
    *,
    candidates: Iterable[object],
    feed_article_ids: Sequence[int] | None,
    feed_source_id: int | None,
    feed_source_ids: Sequence[int] | None,
) -> ArticleFormatProfile | None:
    """在已加载候选中选择任务应锁定的模板。

    用户明确选择某篇文章时，这篇文章的最新模板是唯一允许的自动结果；若它尚未
    完成格式分析则返回空，不能退回同一来源的另一篇文章而悄悄改变版式。只选择
    投喂源时才按文章 ID 和模板版本选择最新候选。
    """

    # 数据库查询通过与 FeedSourceArticle 的内连接只会返回投喂文章来源模板；这里再做
    # 一层领域校验，防止未来新增候选来源或测试替身绕过查询层，绑定无来源文章的异常
    # 记录到正式任务。
    normalized_candidates = [
        candidate
        for candidate in candidates
        if int(
            getattr(getattr(candidate, "profile", None), "source_article_id", 0)
            or 0
        ) > 0
    ]
    article_ids = _normalize_ids(feed_article_ids)
    if article_ids:
        for article_id in article_ids:
            article_candidates = [
                candidate
                for candidate in normalized_candidates
                if int(getattr(getattr(candidate, "profile", None), "source_article_id", 0) or 0)
                == article_id
            ]
            if article_candidates:
                return _latest_profile(article_candidates)
        return None

    for source_id in _ordered_source_ids(feed_source_id, feed_source_ids):
        source_candidates = [
            candidate
            for candidate in normalized_candidates
            if int(getattr(candidate, "feed_source_id", 0) or 0) == source_id
        ]
        if source_candidates:
            return _latest_profile(source_candidates)
    return None


def _latest_profile(candidates: Sequence[object]) -> ArticleFormatProfile:
    """按源文章最新、同文模板版本最新的规则选择最终模板。"""

    latest = max(
        candidates,
        key=lambda candidate: (
            int(getattr(getattr(candidate, "profile", None), "source_article_id", 0) or 0),
            int(getattr(getattr(candidate, "profile", None), "version", 0) or 0),
            int(getattr(getattr(candidate, "profile", None), "id", 0) or 0),
        ),
    )
    return getattr(latest, "profile")


def _normalize_ids(values: Sequence[int] | None) -> list[int]:
    """过滤前端 JSON 字段中的空值和重复值，保持用户原有选择顺序。"""

    normalized: list[int] = []
    for value in values or []:
        try:
            identifier = int(value)
        except (TypeError, ValueError):
            continue
        if identifier > 0 and identifier not in normalized:
            normalized.append(identifier)
    return normalized


def _ordered_source_ids(
    feed_source_id: int | None,
    feed_source_ids: Sequence[int] | None,
) -> list[int]:
    """标量主投喂源优先，其余来源按前端选择顺序回退。"""

    preferred = _normalize_ids([feed_source_id])
    for source_id in _normalize_ids(feed_source_ids):
        if source_id not in preferred:
            preferred.append(source_id)
    return preferred
