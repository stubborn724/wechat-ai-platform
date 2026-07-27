"""Statistics routes"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import AgentLog, Article, ContentJob, WeChatAccount

router = APIRouter()


# --- Schemas ---

class DashboardStats(BaseModel):
    total_accounts: int
    active_jobs: int
    total_articles: int
    articles_by_status: dict
    jobs_by_status: dict
    recent_activity: List[dict]


class AgentLogResponse(BaseModel):
    id: int
    task_id: str
    agent_name: str
    status: str
    prompt: Optional[str] = None
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    error_message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None

    model_config = {"from_attributes": True}


class AgentLogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AgentLogResponse]


# --- Routes ---

@router.get("/statistics/dashboard", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get dashboard statistics overview scoped to current tenant."""
    tid = principal.tenant_id
    total_accounts = db.query(func.count(WeChatAccount.id)).filter(
        WeChatAccount.tenant_id == tid,
    ).scalar() or 0
    active_jobs = db.query(func.count(ContentJob.id)).filter(
        ContentJob.tenant_id == tid,
        ContentJob.status.in_(["pending", "queued", "processing"]),
    ).scalar() or 0
    total_articles = db.query(func.count(Article.id)).filter(
        Article.tenant_id == tid,
    ).scalar() or 0

    # Articles grouped by status
    article_status_rows = (
        db.query(Article.status, func.count(Article.id))
        .filter(Article.tenant_id == tid)
        .group_by(Article.status)
        .all()
    )
    articles_by_status = {row[0]: row[1] for row in article_status_rows}

    # Jobs grouped by status
    job_status_rows = (
        db.query(ContentJob.status, func.count(ContentJob.id))
        .filter(ContentJob.tenant_id == tid)
        .group_by(ContentJob.status)
        .all()
    )
    jobs_by_status = {row[0]: row[1] for row in job_status_rows}

    # Recent activity (last 10 agent log entries scoped via Article tenant_id)
    recent_logs = (
        db.query(AgentLog)
        .join(Article, AgentLog.task_id == Article.task_id)
        .filter(Article.tenant_id == tid)
        .order_by(AgentLog.id.desc())
        .limit(10)
        .all()
    )
    recent_activity = [
        {
            "id": log.id,
            "task_id": log.task_id,
            "agent_name": log.agent_name,
            "status": log.status,
            "created_at": log.start_time.isoformat() if log.start_time else None,
        }
        for log in recent_logs
    ]

    return DashboardStats(
        total_accounts=total_accounts,
        active_jobs=active_jobs,
        total_articles=total_articles,
        articles_by_status=articles_by_status,
        jobs_by_status=jobs_by_status,
        recent_activity=recent_activity,
    )


@router.get("/statistics/agent-logs", response_model=AgentLogListResponse)
def get_agent_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get agent execution logs with pagination and filters, scoped to current tenant."""
    query = db.query(AgentLog).join(
        Article, AgentLog.task_id == Article.task_id
    ).filter(Article.tenant_id == principal.tenant_id)

    if agent_name:
        query = query.filter(AgentLog.agent_name == agent_name)
    if status:
        query = query.filter(AgentLog.status == status)
    if task_id:
        query = query.filter(AgentLog.task_id == task_id)

    total = query.count()
    items = (
        query.order_by(AgentLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return AgentLogListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/statistics/articles/quality-distribution")
def get_quality_distribution(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取文章质量分布统计"""
    from sqlalchemy import func as sa_func

    tid = principal.tenant_id
    # 评分等级
    excellent = db.query(sa_func.count(Article.id)).filter(
        Article.tenant_id == tid,
        Article.latest_quality_score >= 85,
    ).scalar() or 0
    good = db.query(sa_func.count(Article.id)).filter(
        Article.tenant_id == tid,
        Article.latest_quality_score.between(70, 84),
    ).scalar() or 0
    fair = db.query(sa_func.count(Article.id)).filter(
        Article.tenant_id == tid,
        Article.latest_quality_score.between(50, 69),
    ).scalar() or 0
    poor = db.query(sa_func.count(Article.id)).filter(
        Article.tenant_id == tid,
        Article.latest_quality_score < 50,
    ).scalar() or 0
    not_evaluated = db.query(sa_func.count(Article.id)).filter(
        Article.tenant_id == tid,
        Article.latest_quality_score.is_(None),
    ).scalar() or 0

    # 各维度平均分（限定当前租户）
    from app.models.mysql_models import ArticleQualityEvaluation

    avg_row = (
        db.query(
            sa_func.avg(ArticleQualityEvaluation.content_score),
            sa_func.avg(ArticleQualityEvaluation.readability_score),
            sa_func.avg(ArticleQualityEvaluation.structure_score),
            sa_func.avg(ArticleQualityEvaluation.value_score),
            sa_func.avg(ArticleQualityEvaluation.title_score),
        )
        .join(Article, ArticleQualityEvaluation.article_id == Article.id)
        .filter(
            ArticleQualityEvaluation.status == "success",
            Article.tenant_id == tid,
        )
        .first()
    )

    return {
        "excellent": excellent,
        "good": good,
        "fair": fair,
        "poor": poor,
        "not_evaluated": not_evaluated,
        "total": excellent + good + fair + poor + not_evaluated,
        "avg_dimensions": {
            "content_score": round(avg_row[0] or 0, 1),
            "readability_score": round(avg_row[1] or 0, 1),
            "structure_score": round(avg_row[2] or 0, 1),
            "value_score": round(avg_row[3] or 0, 1),
            "title_score": round(avg_row[4] or 0, 1),
        } if avg_row else None,
    }


@router.get("/statistics/articles/optimization-report")
def get_optimization_report(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取优化效果报告"""
    from app.models.mysql_models import ArticleOptimization

    tid = principal.tenant_id
    base = db.query(ArticleOptimization).filter(
        ArticleOptimization.tenant_id == tid,
    )

    total = base.count()
    approved = base.filter(ArticleOptimization.status == "approved").count()
    rejected = base.filter(ArticleOptimization.status == "rejected").count()
    draft_ready = base.filter(ArticleOptimization.status == "draft_ready").count()

    effective = base.filter(
        ArticleOptimization.comparison_result == "effective"
    ).count()
    ineffective = base.filter(
        ArticleOptimization.comparison_result == "ineffective"
    ).count()

    return {
        "total_optimizations": total,
        "draft_ready": draft_ready,
        "approved": approved,
        "rejected": rejected,
        "effective": effective,
        "ineffective": ineffective,
        "pending_review": draft_ready,
    }
