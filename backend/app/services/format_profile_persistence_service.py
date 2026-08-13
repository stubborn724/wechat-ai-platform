"""投喂文章格式模板的版本化持久化服务。

本模块位于格式分析器和业务入口之间：格式分析器只负责从 HTML 得到结构，抓取、
手动重分析等入口则统一通过本服务决定是否复用当前模板或创建下一版本。这样可以
避免每个入口各自实现版本计算，导致同一篇未变化文章在重复抓取时不断生成模板。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models.mysql_models import ArticleFormatProfile, FeedSourceArticle
from app.services.format_profile_service import analyze_feed_article_format


@dataclass(frozen=True)
class FormatProfileSnapshot:
    """一次格式分析产生的不可变快照。

    快照只包含渲染器需要的结构化数据和源 HTML 指纹。源指纹写入 JSON 而不是新增
    数据库列，是为了兼容已经迁移过的测试环境；渲染器会忽略该元数据，因此不会把
    版本控制细节泄漏到模型提示词或最终 HTML。
    """

    name: str
    render_mode: str
    template_payload: dict[str, Any]
    title_policy: dict[str, Any]


@dataclass(frozen=True)
class FormatProfilePersistenceResult:
    """格式模板持久化结果，供抓取流程统计自动创建次数。"""

    profile: ArticleFormatProfile
    created: bool


def build_format_profile_snapshot(
    *,
    article_id: int,
    article_title: str,
    body_html: str,
) -> FormatProfileSnapshot:
    """将投喂 HTML 转换为带内容指纹的格式快照。

    指纹以原始 HTML 为输入，因为正文文字、DOM 和图片顺序任意一个变化都可能影响
    仿写模板；不依赖文章标题，标题修改不会无意义地制造新模板版本。
    """

    normalized_html = str(body_html or "").strip()
    if not normalized_html:
        raise ValueError("格式模板需要投喂文章的 HTML 内容")

    analyzed = analyze_feed_article_format(
        article_id=article_id,
        article_title=article_title,
        body_html=normalized_html,
    )
    template_payload = dict(analyzed.template_payload)
    template_payload["source_fingerprint"] = _build_source_fingerprint(normalized_html)
    return FormatProfileSnapshot(
        name=analyzed.name,
        render_mode=analyzed.render_mode,
        template_payload=template_payload,
        title_policy=dict(analyzed.title_policy),
    )


def is_same_format_profile_snapshot(
    existing_profile: object,
    snapshot: FormatProfileSnapshot,
) -> bool:
    """判断数据库模板是否与最新分析快照完全等价。

    早期手动生成的模板没有 ``source_fingerprint``，此时仍比较去除元数据后的模板
    内容，避免系统启用自动分析后立即为所有旧文章重复生成同构版本。
    """

    existing_payload = getattr(existing_profile, "template_payload", None)
    existing_title_policy = getattr(existing_profile, "title_policy", None)
    return (
        str(getattr(existing_profile, "render_mode", "")) == snapshot.render_mode
        and _without_source_fingerprint(existing_payload)
        == _without_source_fingerprint(snapshot.template_payload)
        and _normalize_mapping(existing_title_policy) == _normalize_mapping(snapshot.title_policy)
    )


def next_format_profile_version(existing_profile: object | None) -> int:
    """根据当前最新模板计算下一不可变版本号。"""

    if existing_profile is None:
        return 1
    return int(getattr(existing_profile, "version", 0) or 0) + 1


def create_or_reuse_format_profile(
    db: Session,
    *,
    article: FeedSourceArticle,
) -> FormatProfilePersistenceResult:
    """为文章创建或复用当前格式模板。

    本函数只 ``flush`` 而不 ``commit``，使调用方能够把“保存文章”和“创建模板”放入
    同一事务：抓取时任何文章的格式分析失败只记录警告并继续处理其他文章，而手动
    重分析接口也能自行决定 HTTP 错误和事务边界。
    """

    snapshot = build_format_profile_snapshot(
        article_id=int(article.id),
        article_title=str(article.title or "未命名投喂文章"),
        body_html=str(article.body_html or ""),
    )
    latest_profile = (
        db.query(ArticleFormatProfile)
        .filter(
            ArticleFormatProfile.tenant_id == article.tenant_id,
            ArticleFormatProfile.source_article_id == article.id,
        )
        .order_by(ArticleFormatProfile.version.desc(), ArticleFormatProfile.id.desc())
        .first()
    )
    if latest_profile is not None and is_same_format_profile_snapshot(
        latest_profile,
        snapshot,
    ):
        return FormatProfilePersistenceResult(profile=latest_profile, created=False)

    profile = ArticleFormatProfile(
        tenant_id=article.tenant_id,
        source_article_id=article.id,
        name=snapshot.name,
        version=next_format_profile_version(latest_profile),
        render_mode=snapshot.render_mode,
        template_payload=snapshot.template_payload,
        title_policy=snapshot.title_policy,
    )
    db.add(profile)
    db.flush()
    return FormatProfilePersistenceResult(profile=profile, created=True)


def _build_source_fingerprint(body_html: str) -> str:
    """生成稳定的源 HTML 指纹，避免存储原文副本用于版本判定。"""

    return sha256(body_html.encode("utf-8")).hexdigest()


def _without_source_fingerprint(payload: object) -> dict[str, Any]:
    """去除持久化元数据后比较实际渲染结构。"""

    normalized = _normalize_mapping(payload)
    normalized.pop("source_fingerprint", None)
    return normalized


def _normalize_mapping(value: object) -> dict[str, Any]:
    """安全转换 JSON 字段，旧数据异常时按空对象比较而不是抛出属性错误。"""

    if not isinstance(value, Mapping):
        return {}
    return dict(value)
