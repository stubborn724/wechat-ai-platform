"""Feed source CRUD — fetch, analyze, and manage articles for imitation."""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ArticleFormatProfile, FeedSource, FeedSourceArticle

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class FeedSourceCreate(BaseModel):
    name: str
    slug: str
    source_type: str  # rss, url, official_account, manual
    source_identifier: str
    feed_url: Optional[str] = None
    style_profile: Optional[dict] = None
    fetch_interval_minutes: int = 60


class FeedSourceUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = None
    source_identifier: Optional[str] = None
    feed_url: Optional[str] = None
    style_profile: Optional[dict] = None
    fetch_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class FeedSourceResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    slug: str
    source_type: str
    source_identifier: str
    feed_url: Optional[str] = None
    status: str
    style_profile: Optional[dict] = None
    last_fetched_at: Optional[datetime] = None
    fetch_interval_minutes: Optional[int] = None
    is_active: bool
    article_count: Optional[int] = None
    # 新建链接投喂源会立即抓取。保留结果摘要，前端无需再额外请求一次才能知道
    # 格式模板是否已经自动建立；字典字段避免响应模型与下方抓取响应发生前向依赖。
    initial_fetch: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FeedSourceListResponse(BaseModel):
    total: int
    items: List[FeedSourceResponse]


class FetchResultResponse(BaseModel):
    source_id: int
    source_name: str
    articles_fetched: int
    articles_saved: int
    format_profiles_created: int = 0
    format_profile_errors: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class AnalyzeResultResponse(BaseModel):
    source_id: int
    articles_analyzed: int
    profile: Optional[dict] = None
    error: Optional[str] = None


class FeedSourceArticleResponse(BaseModel):
    id: int
    title: Optional[str] = None
    article_url: Optional[str] = None
    summary: Optional[str] = None
    body_markdown: Optional[str] = None
    cover_image_url: Optional[str] = None
    word_count: Optional[int] = None
    is_analyzed: bool
    # is_analyzed 表示写作风格分析；格式模板是独立生命周期，必须单独返回，避免
    # 自动格式分析完成后仍在界面显示“未分析”。
    format_profile_id: Optional[int] = None
    format_profile_name: Optional[str] = None
    format_profile_version: Optional[int] = None
    format_render_mode: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ArticleFormatProfileResponse(BaseModel):
    """供投喂源和定时任务页面选择的格式模板摘要。"""

    id: int
    source_article_id: Optional[int] = None
    name: str
    version: int
    render_mode: str
    is_active: bool
    source_article_title: Optional[str] = None
    source_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedSourceArticleListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[FeedSourceArticleResponse]


class AddArticleRequest(BaseModel):
    title: str
    body_markdown: str
    summary: Optional[str] = None


# --- Helpers ---

def _enrich_source(source: FeedSource, db: Session) -> FeedSourceResponse:
    """Add computed article_count to response."""
    resp = FeedSourceResponse.model_validate(source)
    resp.article_count = (
        db.query(FeedSourceArticle)
        .filter(FeedSourceArticle.feed_source_id == source.id)
        .count()
    )
    return resp


# --- Routes ---

@router.get("/feed-sources", response_model=FeedSourceListResponse)
def list_feed_sources(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List all feed sources."""
    sources = db.query(FeedSource).filter(FeedSource.tenant_id == principal.tenant_id).order_by(FeedSource.id.desc()).all()
    items = [_enrich_source(s, db) for s in sources]
    return FeedSourceListResponse(total=len(items), items=items)


@router.post("/feed-sources", response_model=FeedSourceResponse,
             status_code=status.HTTP_201_CREATED)
async def create_feed_source(
    req: FeedSourceCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """创建投喂源并立即导入链接文章，完成首次仿写模板分析闭环。

    用户填写的是一篇可抓取链接时，无需再返回页面手动点击“抓取”和“分析为格式
    模板”。抓取内部会把单篇格式错误收敛为警告，所以这里仍能返回已创建的投喂源。
    """
    existing = db.query(FeedSource).filter(FeedSource.slug == req.slug, FeedSource.tenant_id == principal.tenant_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Feed source with slug '{req.slug}' already exists",
        )

    source = FeedSource(
        tenant_id=principal.tenant_id,
        name=req.name,
        slug=req.slug,
        source_type=req.source_type,
        source_identifier=req.source_identifier,
        feed_url=req.feed_url,
        style_profile=req.style_profile,
        fetch_interval_minutes=req.fetch_interval_minutes,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    from app.services.feed_service import fetch_source

    initial_fetch = await fetch_source(
        db,
        source.id,
        tenant_id=principal.tenant_id,
    )
    response = _enrich_source(source, db)
    response.initial_fetch = initial_fetch
    return response


@router.get("/feed-sources/{source_id}", response_model=FeedSourceResponse)
def get_feed_source(
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get a single feed source by id."""
    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")
    return _enrich_source(source, db)


@router.put("/feed-sources/{source_id}", response_model=FeedSourceResponse)
def update_feed_source(
    source_id: int,
    req: FeedSourceUpdate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update a feed source."""
    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(source, field, value)

    db.commit()
    db.refresh(source)
    return _enrich_source(source, db)


@router.delete("/feed-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feed_source(
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a feed source."""
    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    # Delete associated articles
    db.query(FeedSourceArticle).filter(
        FeedSourceArticle.feed_source_id == source_id
    ).delete()
    db.delete(source)
    db.commit()


# --- Feed operations ---

@router.post("/feed-sources/{source_id}/fetch", response_model=FetchResultResponse)
async def trigger_fetch(
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Fetch articles from a feed source.

    Supports RSS feeds (feed_url) and single URLs (source_identifier).
    For WeChat official accounts, provide the article listing URL.
    """
    from app.services.feed_service import fetch_source

    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    result = await fetch_source(db, source_id, tenant_id=principal.tenant_id)
    return FetchResultResponse(**result)


@router.post("/feed-sources/{source_id}/analyze", response_model=AnalyzeResultResponse)
async def analyze_style(
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Analyze the writing style of fetched articles.

    Uses AI to extract tone, vocabulary level, sentence structure, and other
    style characteristics. Results are saved to the feed source's style_profile.
    Use this profile for imitation writing.
    """
    from app.services.feed_service import analyze_source_style

    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    result = await analyze_source_style(db, source_id, tenant_id=principal.tenant_id)
    return AnalyzeResultResponse(**result)


# --- Article management ---

@router.get("/feed-sources/{source_id}/articles",
            response_model=FeedSourceArticleListResponse)
def list_articles(
    source_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    analyzed: Optional[bool] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List articles for a feed source."""
    from app.services.feed_service import list_source_articles

    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    items = list_source_articles(db, source_id, analyzed=analyzed,
                                  page=page, page_size=page_size)
    total = db.query(FeedSourceArticle).filter(
        FeedSourceArticle.feed_source_id == source_id
    ).count()

    article_ids = [item.id for item in items]
    latest_profiles = {}
    if article_ids:
        profiles = (
            db.query(ArticleFormatProfile)
            .filter(
                ArticleFormatProfile.tenant_id == principal.tenant_id,
                ArticleFormatProfile.source_article_id.in_(article_ids),
                ArticleFormatProfile.is_active == True,  # noqa: E712
            )
            .order_by(
                ArticleFormatProfile.version.desc(),
                ArticleFormatProfile.id.desc(),
            )
            .all()
        )
        for profile in profiles:
            latest_profiles.setdefault(profile.source_article_id, profile)

    response_items = []
    for item in items:
        response_item = FeedSourceArticleResponse.model_validate(item)
        profile = latest_profiles.get(item.id)
        if profile is not None:
            response_item.format_profile_id = profile.id
            response_item.format_profile_name = profile.name
            response_item.format_profile_version = profile.version
            response_item.format_render_mode = profile.render_mode
        response_items.append(response_item)

    return FeedSourceArticleListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=response_items,
    )


@router.post("/feed-sources/{source_id}/articles",
             response_model=FeedSourceArticleResponse,
             status_code=status.HTTP_201_CREATED)
def add_article(
    source_id: int,
    req: AddArticleRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Manually add an article to a feed source."""
    from app.services.feed_service import add_manual_article

    source = db.query(FeedSource).filter(FeedSource.id == source_id, FeedSource.tenant_id == principal.tenant_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    fa = add_manual_article(
        db=db, source_id=source_id, tenant_id=principal.tenant_id,
        title=req.title, body_markdown=req.body_markdown,
        summary=req.summary,
    )
    return fa


@router.post(
    "/feed-sources/{source_id}/articles/{article_id}/format-profiles",
    response_model=ArticleFormatProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_article_format(
    source_id: int,
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """将一篇投喂文章一次性分析为新的格式模板版本。

    用户必须主动点击该入口，普通投喂源只做选题和风格参考时不会产生模板，更不会
    改变已绑定任务。每次分析创建新版本，已发布或正在运行的任务保持原模板版本。
    """

    article = (
        db.query(FeedSourceArticle)
        .filter(
            FeedSourceArticle.id == article_id,
            FeedSourceArticle.feed_source_id == source_id,
            FeedSourceArticle.tenant_id == principal.tenant_id,
        )
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="投喂文章不存在")
    from app.services.format_profile_persistence_service import (
        create_or_reuse_format_profile,
    )

    try:
        persisted = create_or_reuse_format_profile(db, article=article)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(persisted.profile)
    return persisted.profile


@router.get("/format-profiles", response_model=List[ArticleFormatProfileResponse])
def list_format_profiles(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出当前租户可供测试任务显式绑定的格式模板。"""

    rows = (
        db.query(ArticleFormatProfile, FeedSourceArticle.title, FeedSource.name)
        .join(
            FeedSourceArticle,
            FeedSourceArticle.id == ArticleFormatProfile.source_article_id,
        )
        .join(FeedSource, FeedSource.id == FeedSourceArticle.feed_source_id)
        .filter(
            ArticleFormatProfile.tenant_id == principal.tenant_id,
            ArticleFormatProfile.source_article_id.isnot(None),
            FeedSourceArticle.tenant_id == principal.tenant_id,
            FeedSource.tenant_id == principal.tenant_id,
            ArticleFormatProfile.is_active == True,  # noqa: E712
        )
        .order_by(ArticleFormatProfile.updated_at.desc(), ArticleFormatProfile.id.desc())
        .all()
    )
    response_items = []
    for profile, article_title, source_name in rows:
        item = ArticleFormatProfileResponse.model_validate(profile)
        item.source_article_title = article_title
        item.source_name = source_name
        response_items.append(item)
    return response_items
