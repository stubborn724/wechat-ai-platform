"""Content job pipeline routes — queue management, batch generation, transitions."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ContentAsset, ContentJob, ContentVersion, PublishAttempt
from app.services.job_queue_service import (
    claim_queued_job_for_dispatch,
    create_slot_articles,
    release_dispatch_claim,
    transition_job,
    validate_transition,
)

def _get_job_or_404(db: Session, job_id: int, tenant_id: int) -> ContentJob:
    """Get a content job scoped to tenant, or raise 404."""
    job = db.query(ContentJob).filter(
        ContentJob.id == job_id,
        ContentJob.tenant_id == tenant_id,
    ).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")
    return job

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
    # 纯图片/视频配置快捷字段（自动合并到 generation_config）
    aspect_ratio: Optional[str] = None  # 图片/视频比例: 1:1, 3:4, 9:16, 16:9
    duration_sec: Optional[int] = None  # 视频时长（秒）
    storyboard_count: Optional[int] = None  # 视频分镜数量
    brand_style: Optional[str] = None  # 品牌风格

    target_audience: Optional[str] = None  # 目标用户
    extra_notes: Optional[str] = None  # 补充说明


class JobTransitionRequest(BaseModel):
    action: str  # e.g. "queue", "cancel", "approve", "reject"


class PublishAttemptResponse(BaseModel):
    id: int
    account_id: int
    mode: str
    status: str
    platform_media_id: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


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
    publish_attempts: List[PublishAttemptResponse] = []
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


def _attach_publish_attempts(db: Session, resp: JobResponse) -> JobResponse:
    """Load PublishAttempt records for a job response."""
    attempts = db.query(PublishAttempt).filter(
        PublishAttempt.job_id == resp.id
    ).order_by(PublishAttempt.account_id).all()
    resp.publish_attempts = [PublishAttemptResponse.model_validate(a) for a in attempts]
    return resp


# --- Routes ---

@router.get("/content-jobs", response_model=JobListResponse)
def list_content_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List content jobs for the current tenant with pagination and optional status filter."""
    query = db.query(ContentJob).filter(ContentJob.tenant_id == principal.tenant_id)

    if status:
        query = query.filter(ContentJob.status == status)

    total = query.count()
    items = query.order_by(ContentJob.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    jobs = [JobResponse.model_validate(j) for j in items]
    jobs = [_attach_publish_attempts(db, j) for j in jobs]
    return JobListResponse(total=total, page=page, page_size=page_size, items=jobs)


@router.post("/content-jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_content_job(
    req: JobCreateRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new content job for batch generation."""
    existing = db.query(ContentJob).filter(
        ContentJob.idempotency_key == req.idempotency_key,
        ContentJob.tenant_id == principal.tenant_id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job with idempotency_key '{req.idempotency_key}' already exists",
        )

    config = req.generation_config or {}
    if req.article_count > 1 and "article_count" not in config:
        config["article_count"] = req.article_count

    # 合并快捷配置字段到 generation_config
    for field in ("aspect_ratio", "duration_sec", "storyboard_count",
                  "brand_style", "target_audience", "extra_notes"):
        val = getattr(req, field, None)
        if val is not None and field not in config:
            config[field] = val

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
    """Get content job detail with publish attempts."""
    job = _get_job_or_404(db, job_id, principal.tenant_id)
    resp = JobResponse.model_validate(job)
    return _attach_publish_attempts(db, resp)


@router.delete("/content-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content_job(
    job_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a content job (only allowed if status is cancelled or failed)."""
    job = _get_job_or_404(db, job_id, principal.tenant_id)
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
    job = _get_job_or_404(db, job_id, principal.tenant_id)

    try:
        validate_transition(job, req.action)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if req.action == "queue":
        # Create slot articles for batch processing
        create_slot_articles(db, job)
        # 先提交 queued，再通过条件更新领取。旧顺序会让 Worker 在任务仍为 pending 时启动，
        # 也会让 Beat 与 HTTP 入口重复发派同一个 Job。
        job = transition_job(db, job_id, "queue")
        claimed = claim_queued_job_for_dispatch(db, job_id)
        if not claimed:
            db.refresh(job)
            return job
        content_type = job.content_type or "article"
        logger.info("Dispatching claimed job %d with content_type=%s to Celery", job_id, content_type)
        try:
            if content_type in ("image", "pure_image"):
                from app.tasks.content_tasks import process_image_job
                process_image_job.delay(job_id)
                logger.info("Dispatched job %d to process_image_job", job_id)
            elif content_type == "video":
                from app.tasks.content_tasks import process_video_job
                process_video_job.delay(job_id)
                logger.info("Dispatched job %d to process_video_job", job_id)
            else:
                from app.tasks.job_tasks import process_content_job
                process_content_job.delay(job_id)
                logger.info("Dispatched job %d to process_content_job", job_id)
        except Exception as exc:
            logger.error("Failed to dispatch job %d to Celery: %s", job_id, exc)
            # 仅释放仍未被 Worker 取得的领取状态；消息实际已到达时不能把 generating 覆盖回 queued。
            release_dispatch_claim(db, job_id)
        job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
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
    """List content versions for a job, scoped to current tenant."""
    job = _get_job_or_404(db, job_id, principal.tenant_id)

    versions = (
        db.query(ContentVersion)
        .filter(
            ContentVersion.job_id == job_id,
            ContentVersion.tenant_id == principal.tenant_id,
        )
        .order_by(ContentVersion.version_number.desc())
        .all()
    )
    return versions

