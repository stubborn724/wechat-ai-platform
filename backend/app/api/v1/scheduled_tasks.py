"""Unified scheduled task CRUD — replaces PublishPlan + ImitationTask"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ScheduledTask

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class ArticleSlot(BaseModel):
    content_type: str = "image_text"  # image_text / video / pure_image
    publish_domain: str = "public"    # public / private


class ScheduledTaskCreate(BaseModel):
    name: str
    writing_mode: str = "free"        # free / feed / kb
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None  # 直接关联投喂源，替代仿写池
    style: Optional[str] = None       # 写作风格
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: int = -1
    publish_times: List[str]
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: int = 1
    public_count: int = 1
    private_count: int = 0
    approval_mode: str = "auto"
    account_id: Optional[int] = None
    footer_template: Optional[str] = None


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    writing_mode: Optional[str] = None
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None
    style: Optional[str] = None
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: Optional[int] = None
    publish_times: Optional[List[str]] = None
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: Optional[int] = None
    public_count: Optional[int] = None
    private_count: Optional[int] = None
    approval_mode: Optional[str] = None
    account_id: Optional[int] = None
    footer_template: Optional[str] = None


class ScheduledTaskResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    is_active: bool
    writing_mode: str
    topic: Optional[str] = None
    feed_source_ids: Optional[list] = None
    style: Optional[str] = None
    knowledge_base_ids: Optional[list] = None
    day_of_week: int
    publish_times: list
    article_slots: Optional[list] = None
    articles_per_day: int
    public_count: int
    private_count: int
    approval_mode: str
    account_id: Optional[int] = None
    footer_template: Optional[str] = None
    total_generated: int
    last_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduledTaskListResponse(BaseModel):
    total: int
    items: List[ScheduledTaskResponse]


# --- Routes ---

@router.get("/scheduled-tasks", response_model=ScheduledTaskListResponse)
def list_scheduled_tasks(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List all scheduled tasks."""
    items = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.tenant_id == principal.tenant_id)
        .order_by(ScheduledTask.id.desc())
        .all()
    )
    return ScheduledTaskListResponse(total=len(items), items=items)


@router.post("/scheduled-tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new scheduled task."""
    if req.day_of_week not in range(-1, 7):
        raise HTTPException(status_code=400, detail="day_of_week must be -1 (every day) or 0-6")

    slots = [s.model_dump() for s in req.article_slots] if req.article_slots else None

    task = ScheduledTask(
        tenant_id=principal.tenant_id,
        name=req.name,
        writing_mode=req.writing_mode,
        topic=req.topic,
        feed_source_ids=req.feed_source_ids,
        style=req.style,
        knowledge_base_ids=req.knowledge_base_ids,
        day_of_week=req.day_of_week,
        publish_times=req.publish_times,
        article_slots=slots,
        articles_per_day=req.articles_per_day,
        public_count=req.public_count,
        private_count=req.private_count,
        approval_mode=req.approval_mode,
        account_id=req.account_id,
        footer_template=req.footer_template,
        created_by=principal.user_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.put("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def update_scheduled_task(
    task_id: int,
    req: ScheduledTaskUpdate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update a scheduled task."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    update_data = req.model_dump(exclude_unset=True)
    if "article_slots" in update_data and update_data["article_slots"] is not None:
        update_data["article_slots"] = [s.model_dump() for s in update_data["article_slots"]]

    for field, value in update_data.items():
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return task


@router.delete("/scheduled-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scheduled_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a scheduled task."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    db.delete(task)
    db.commit()


@router.post("/scheduled-tasks/{task_id}/toggle", response_model=ScheduledTaskResponse)
def toggle_scheduled_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Toggle task active/inactive."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    task.is_active = not task.is_active
    db.commit()
    db.refresh(task)
    return task
