"""微信文章同步 API — 拉取公众号草稿箱 & 已发布文章"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import WeChatSyncedArticle

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================


class SyncedArticleResponse(BaseModel):
    id: int
    account_id: int
    article_type: str
    media_id: Optional[str] = None
    wechat_article_id: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    digest: Optional[str] = None
    cover_url: Optional[str] = None
    wechat_url: Optional[str] = None
    content: Optional[str] = None
    publish_time: Optional[datetime] = None
    need_open_comment: int = 0
    msg_data_id: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncedArticleListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[SyncedArticleResponse]


class SyncResultResponse(BaseModel):
    synced: int
    total: int
    account_id: int
    type: str


# ============================================================================
# API
# ============================================================================


@router.get("/wechat-articles", response_model=SyncedArticleListResponse)
def list_synced_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    account_id: Optional[int] = Query(None),
    article_type: Optional[str] = Query(None, description="draft / published"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """查询本地已同步的微信文章列表"""
    from app.services.wechat_article_sync_service import get_local_articles

    items, total = get_local_articles(
        db, principal.tenant_id,
        account_id=account_id,
        article_type=article_type,
        page=page,
        page_size=page_size,
    )
    return SyncedArticleListResponse(
        total=total, page=page, page_size=page_size, items=items,
    )


@router.post("/wechat-articles/sync-drafts", response_model=SyncResultResponse)
async def sync_wechat_drafts(
    account_id: int = Query(..., description="公众号 ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """同步指定公众号的草稿箱"""
    from app.services.wechat_article_sync_service import sync_drafts

    try:
        result = await sync_drafts(db, principal.tenant_id, account_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.post("/wechat-articles/sync-published", response_model=SyncResultResponse)
async def sync_wechat_published(
    account_id: int = Query(..., description="公众号 ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """同步指定公众号的已发布文章"""
    from app.services.wechat_article_sync_service import sync_published

    try:
        result = await sync_published(db, principal.tenant_id, account_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.get("/wechat-articles/{article_id}", response_model=SyncedArticleResponse)
async def get_synced_article(
    article_id: int,
    fetch_content: bool = Query(False, description="是否实时拉取正文"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取同步文章的详情（可选拉取正文）"""
    from app.services.wechat_article_sync_service import get_article_detail

    article = await get_article_detail(db, article_id, fetch_content=fetch_content)
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if article.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return article


@router.delete("/wechat-articles/{article_id}")
def delete_synced_article(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """软删除本地同步记录"""
    article = db.query(WeChatSyncedArticle).filter(
        WeChatSyncedArticle.id == article_id,
        WeChatSyncedArticle.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    article.is_deleted = True
    db.commit()
    return {"success": True}
