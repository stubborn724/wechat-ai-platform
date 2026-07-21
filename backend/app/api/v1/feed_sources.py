"""Feed source CRUD — fetch, analyze, and manage articles for imitation."""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import FeedSource, FeedSourceArticle

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
    errors: List[str] = []


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
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

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
    sources = db.query(FeedSource).order_by(FeedSource.id.desc()).all()
    items = [_enrich_source(s, db) for s in sources]
    return FeedSourceListResponse(total=len(items), items=items)


@router.post("/feed-sources", response_model=FeedSourceResponse,
             status_code=status.HTTP_201_CREATED)
def create_feed_source(
    req: FeedSourceCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new feed source."""
    existing = db.query(FeedSource).filter(FeedSource.slug == req.slug).first()
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
    return _enrich_source(source, db)


@router.get("/feed-sources/{source_id}", response_model=FeedSourceResponse)
def get_feed_source(
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get a single feed source by id."""
    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
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
    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
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
    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
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

    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    result = await fetch_source(db, source_id)
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

    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    result = await analyze_source_style(db, source_id)
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

    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    items = list_source_articles(db, source_id, analyzed=analyzed,
                                  page=page, page_size=page_size)
    total = db.query(FeedSourceArticle).filter(
        FeedSourceArticle.feed_source_id == source_id
    ).count()

    return FeedSourceArticleListResponse(
        total=total, page=page, page_size=page_size, items=items
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

    source = db.query(FeedSource).filter(FeedSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Feed source not found")

    fa = add_manual_article(
        db=db, source_id=source_id, tenant_id=principal.tenant_id,
        title=req.title, body_markdown=req.body_markdown,
        summary=req.summary,
    )
    return fa
