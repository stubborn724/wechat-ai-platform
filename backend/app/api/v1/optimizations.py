"""文章优化管理路由 — 候选列表、审核、效果查看"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import Article, ArticleOptimization, ArticleQualityEvaluation

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class OptimizationCandidate(BaseModel):
    id: int
    article_id: int
    title: Optional[str] = None
    topic: Optional[str] = None
    quality_score: Optional[int] = None
    optimization_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OptimizationDetail(BaseModel):
    id: int
    source_article_id: int
    optimized_article_id: Optional[int] = None
    optimization_type: str
    optimization_generation: int
    status: str
    change_summary: Optional[str] = None
    reviewer_id: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None
    comparison_result: Optional[str] = None
    comparison_summary: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OptimizationListResponse(BaseModel):
    total: int
    items: List[OptimizationDetail]


# --- Routes ---


@router.get("/optimizations/candidates")
def list_optimization_candidates(
    status: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    max_score: Optional[int] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取优化候选文章列表"""
    query = db.query(Article).filter(
        Article.tenant_id == principal.tenant_id,
        Article.optimization_status.isnot(None),
    )

    if status:
        query = query.filter(Article.optimization_status == status)
    if min_score is not None:
        query = query.filter(Article.latest_quality_score >= min_score)
    if max_score is not None:
        query = query.filter(Article.latest_quality_score <= max_score)

    articles = query.order_by(Article.latest_quality_score.asc()).limit(50).all()
    return [
        {
            "id": a.id,
            "article_id": a.id,
            "title": a.main_title or a.topic,
            "topic": a.topic,
            "quality_score": a.latest_quality_score,
            "optimization_status": a.optimization_status or "unknown",
            "created_at": a.created_at,
        }
        for a in articles
    ]


@router.get("/optimizations", response_model=OptimizationListResponse)
def list_optimizations(
    source_article_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出优化记录"""
    query = db.query(ArticleOptimization).filter(
        ArticleOptimization.tenant_id == principal.tenant_id,
    )
    if source_article_id:
        query = query.filter(ArticleOptimization.source_article_id == source_article_id)
    if status:
        query = query.filter(ArticleOptimization.status == status)

    total = query.count()
    items = query.order_by(ArticleOptimization.id.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return OptimizationListResponse(total=total, items=items)


@router.get("/optimizations/{optimization_id}", response_model=OptimizationDetail)
def get_optimization(
    optimization_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取优化记录详情"""
    opt = db.query(ArticleOptimization).filter(
        ArticleOptimization.id == optimization_id,
        ArticleOptimization.tenant_id == principal.tenant_id,
    ).first()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")
    return opt


@router.post("/optimizations/{optimization_id}/approve")
def approve_optimization(
    optimization_id: int,
    req: dict,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """批准优化稿"""
    opt = db.query(ArticleOptimization).filter(
        ArticleOptimization.id == optimization_id,
        ArticleOptimization.tenant_id == principal.tenant_id,
    ).first()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")

    opt.status = "approved"
    opt.reviewer_id = principal.user_id
    opt.reviewed_at = datetime.utcnow()
    opt.review_comment = req.get("comment", "")

    # 更新优化稿文章状态
    if opt.optimized_article_id:
        article = db.query(Article).filter(
            Article.id == opt.optimized_article_id,
            Article.tenant_id == principal.tenant_id,
        ).first()
        if article:
            article.optimization_status = "approved"
            article.status = "approved"

    db.commit()
    return {"message": "Optimization approved", "id": optimization_id}


@router.post("/optimizations/{optimization_id}/reject")
def reject_optimization(
    optimization_id: int,
    req: dict,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """驳回优化稿"""
    opt = db.query(ArticleOptimization).filter(
        ArticleOptimization.id == optimization_id,
        ArticleOptimization.tenant_id == principal.tenant_id,
    ).first()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")

    opt.status = "rejected"
    opt.reviewer_id = principal.user_id
    opt.reviewed_at = datetime.utcnow()
    opt.review_comment = req.get("comment", "")

    if opt.optimized_article_id:
        article = db.query(Article).filter(
            Article.id == opt.optimized_article_id,
            Article.tenant_id == principal.tenant_id,
        ).first()
        if article:
            article.optimization_status = "rejected"

    db.commit()
    return {"message": "Optimization rejected", "id": optimization_id}


@router.post("/optimizations/{optimization_id}/regenerate")
def regenerate_optimization(
    optimization_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """重新生成优化稿"""
    from app.tasks.optimization_tasks import generate_optimization_draft

    opt = db.query(ArticleOptimization).filter(
        ArticleOptimization.id == optimization_id,
        ArticleOptimization.tenant_id == principal.tenant_id,
    ).first()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")

    opt.status = "needs_revision"
    db.commit()

    generate_optimization_draft.delay(
        opt.source_article_id, opt.optimization_type,
        evaluation_id=opt.trigger_evaluation_id or 0,
    )
    return {"message": "Regeneration triggered", "id": optimization_id}


@router.get("/optimizations/{optimization_id}/comparison")
def get_optimization_comparison(
    optimization_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取优化效果对比"""
    from app.services.optimization_comparison_service import comparison_service

    opt = db.query(ArticleOptimization).filter(
        ArticleOptimization.id == optimization_id,
        ArticleOptimization.tenant_id == principal.tenant_id,
    ).first()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")

    result = comparison_service.compare(db, optimization_id)
    return result
