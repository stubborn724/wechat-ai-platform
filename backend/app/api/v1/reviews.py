"""Content review routes"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ContentJob, ContentVersion, Review
from app.services.job_queue_service import transition_job

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---

class ReviewSubmitRequest(BaseModel):
    job_id: int
    content_version_id: Optional[int] = None
    decision: str  # "approved" or "rejected"
    comment: Optional[str] = None


class ReviewResponse(BaseModel):
    id: int
    tenant_id: int
    job_id: int
    content_version_id: Optional[int] = None
    reviewer_id: int
    decision: str
    comment: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ReviewResponse]


class PendingReviewResponse(BaseModel):
    id: int
    job_id: int
    job_topic: str
    job_status: str
    content_version_id: Optional[int] = None
    latest_version: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Routes ---

@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    decision: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List reviews for the current tenant with pagination and optional decision filter."""
    query = db.query(Review).filter(Review.tenant_id == principal.tenant_id)

    if decision:
        query = query.filter(Review.decision == decision)

    total = query.count()
    items = query.order_by(Review.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return ReviewListResponse(total=total, page=page, page_size=page_size, items=items)


@router.post("/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
def submit_review(
    req: ReviewSubmitRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Submit a review decision (approve/reject) for a content job."""
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be 'approved' or 'rejected'",
        )

    job = db.query(ContentJob).filter(
        ContentJob.id == req.job_id,
        ContentJob.tenant_id == principal.tenant_id,
    ).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content job not found")

    review = Review(
        tenant_id=principal.tenant_id,
        job_id=req.job_id,
        content_version_id=req.content_version_id,
        reviewer_id=principal.user_id,
        decision=req.decision,
        comment=req.comment,
    )
    db.add(review)
    db.commit()

    # Drive the ContentJob state machine based on review decision
    try:
        if req.decision == "approved":
            transition_job(db, req.job_id, "approve")
        elif req.decision == "rejected":
            transition_job(db, req.job_id, "reject")
    except ValueError as e:
        logger.warning("Review decision could not transition job %d: %s", req.job_id, e)

    db.refresh(review)
    return review


@router.get("/reviews/pending", response_model=List[PendingReviewResponse])
def get_pending_reviews(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get pending reviews assigned to or awaiting action from the current user."""
    # Find jobs awaiting review that have no review decision yet for the latest version
    pending_jobs = (
        db.query(ContentJob)
        .filter(
            ContentJob.status == "awaiting_review",
            ContentJob.tenant_id == principal.tenant_id,
            ContentJob.approval_mode == "manual",
        )
        .order_by(ContentJob.updated_at.asc())
        .all()
    )

    # Also match the old "pending_review" status for backward compatibility
    old_pending = (
        db.query(ContentJob)
        .filter(
            ContentJob.status == "pending_review",
            ContentJob.tenant_id == principal.tenant_id,
            ContentJob.approval_mode == "manual",
        )
        .order_by(ContentJob.updated_at.asc())
        .all()
    )
    pending_jobs = pending_jobs + old_pending

    results = []
    for job in pending_jobs:
        latest = (
            db.query(ContentVersion)
            .filter(
                ContentVersion.job_id == job.id,
                ContentVersion.tenant_id == principal.tenant_id,
            )
            .order_by(ContentVersion.version_number.desc())
            .first()
        )
        results.append(
            PendingReviewResponse(
                id=job.id,
                job_id=job.id,
                job_topic=job.topic,
                job_status=job.status,
                content_version_id=latest.id if latest else None,
                latest_version={
                    "id": latest.id,
                    "title": latest.title,
                    "body_markdown": latest.body_markdown,
                    "summary": latest.summary,
                    "version_number": latest.version_number,
                } if latest else None,
                created_at=job.created_at,
            )
        )

    return results
