"""Content job pipeline routes — queue management, batch generation, transitions."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ContentJob, ContentVersion
from app.services.job_queue_service import (
    create_slot_articles,
    transition_job,
    validate_transition,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class JobCreateRequest(BaseModel):
    topic: str
    content_type: str = "article"
    approval_mode: str = "auto"
    scheduled_at: Optional[datetime] = None
    idempotency_key: str
    article_count: int = 1
    generation_config: Optional[dict] = None
    footer_template: Optional[str] = None
    signature_config: Optional[dict] = None


class JobTransitionRequest(BaseModel):
    action: str  # e.g. "queue", "cancel", "approve", "reject"


class JobResponse(BaseModel):
    id: int
    tenant_id: int
    account_id: Optional[int] = None
    status: str
    version: int
    topic: str
    content_type: str
    approval_mode: str
    scheduled_at: Optional[datetime] = None
    idempotency_key: str
    created_by: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    generation_config: Optional[dict] = None
    footer_template: Optional[str] = None
    signature_config: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[JobResponse]


class ContentVersionResponse(BaseModel):
    id: int
    tenant_id: int
    job_id: int
    version_number: int
    title: Optional[str] = None
    body_markdown: Optional[str] = None
    body_html: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[list] = None
    citations: Optional[list] = None
    findings: Optional[list] = None
    model_metadata: Optional[dict] = None
    source: Optional[str] = None
    cover_asset_id: Optional[int] = None
    created_by: Optional[int] = None
    article_id: Optional[int] = None
    article_content_type: Optional[str] = None
    publish_domain: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Routes ---

@router.get("/content-jobs", response_model=JobListResponse)
def list_content_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List content jobs with pagination and optional status filter."""
    query = db.query(ContentJob)

    if status:
        query = query.filter(ContentJob.status == status)

    total = query.count()
    items = query.order_by(ContentJob.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return JobListResponse(total=total, page=page, page_size=page_size, items=items)


@router.post("/content-jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_content_job(
    req: JobCreateRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new content job for batch generation."""
    existing = db.query(ContentJob).filter(
        ContentJob.idempotency_key == req.idempotency_key,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job with idempotency_key '{req.idempotency_key}' already exists",
        )

    config = req.generation_config or {}
    if req.article_count > 1 and "article_count" not in config:
        config["article_count"] = req.article_count

    job = ContentJob(
        tenant_id=principal.tenant_id,
        topic=req.topic,
        content_type=req.content_type,
        approval_mode=req.approval_mode,
        scheduled_at=req.scheduled_at,
        idempotency_key=req.idempotency_key,
        created_by=principal.user_id,
        generation_config=config,
        footer_template=req.footer_template,
        signature_config=req.signature_config,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/content-jobs/{job_id}", response_model=JobResponse)
def get_content_job(
    job_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get content job detail."""
    job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")
    return job


@router.delete("/content-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content_job(
    job_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a content job (only allowed if status is cancelled or failed)."""
    job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")
    if job.status not in ("cancelled", "failed", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete job in status '{job.status}'. Cancel it first.",
        )
    db.delete(job)
    db.commit()


@router.post("/content-jobs/{job_id}/transition")
def transition_content_job(
    job_id: int,
    req: JobTransitionRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Perform state transition on a content job.

    Supported actions: queue, cancel, pause, resume, approve, reject, schedule, publish.
    """
    job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")

    try:
        validate_transition(job, req.action)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if req.action == "queue":
        # Create slot articles for batch processing
        create_slot_articles(db, job)
        job = transition_job(db, job_id, "queue")
        # Dispatch to Celery worker
        try:
            from app.tasks.job_tasks import process_content_job
            process_content_job.delay(job_id)
        except Exception as exc:
            logger.error("Failed to dispatch job %d to Celery: %s", job_id, exc)
    else:
        job = transition_job(db, job_id, req.action)

    db.refresh(job)
    return job


@router.get("/content-jobs/{job_id}/versions", response_model=List[ContentVersionResponse])
def list_content_versions(
    job_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List content versions for a job."""
    job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")

    versions = (
        db.query(ContentVersion)
        .filter(ContentVersion.job_id == job_id)
        .order_by(ContentVersion.version_number.desc())
        .all()
    )
    return versions
