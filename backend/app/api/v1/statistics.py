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
    """Get dashboard statistics overview."""
    total_accounts = db.query(func.count(WeChatAccount.id)).scalar() or 0
    active_jobs = db.query(func.count(ContentJob.id)).filter(
        ContentJob.status.in_(["pending", "queued", "processing"]),
    ).scalar() or 0
    total_articles = db.query(func.count(Article.id)).scalar() or 0

    # Articles grouped by status
    article_status_rows = (
        db.query(Article.status, func.count(Article.id))
        .group_by(Article.status)
        .all()
    )
    articles_by_status = {row[0]: row[1] for row in article_status_rows}

    # Jobs grouped by status
    job_status_rows = (
        db.query(ContentJob.status, func.count(ContentJob.id))
        .group_by(ContentJob.status)
        .all()
    )
    jobs_by_status = {row[0]: row[1] for row in job_status_rows}

    # Recent activity (last 10 agent log entries)
    recent_logs = (
        db.query(AgentLog)
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
    """Get agent execution logs with pagination and filters."""
    query = db.query(AgentLog)

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
