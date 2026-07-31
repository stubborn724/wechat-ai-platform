"""Unified scheduled task CRUD — replaces PublishPlan + ImitationTask"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ScheduledTask, ScheduledTaskSlot

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class ArticleSlot(BaseModel):
    content_type: str = "image_text"  # image_text / video / pure_image
    publish_domain: str = "public"    # public / private


class ScheduledErpImageConfig(BaseModel):
    """定时任务的 ERP 配图策略，分类为空时从来源全部产品中随机选择。"""

    source_key: str
    commodity_category: Optional[str] = None
    repeat_after_days: int = 3
    image_count: int = 8


class ScheduledTaskCreate(BaseModel):
    name: str
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None  # 直接关联投喂源，替代仿写池
    feed_source_id: Optional[int] = None  # 具体选中的投喂源 ID
    feed_article_ids: Optional[List[int]] = None  # 选中的文章 ID 列表
    style: Optional[str] = None       # 写作风格
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: int = -1
    publish_times: List[str]
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: int = 1
    # HTML 版式仿写默认沿用五张图的成本保护；需要更多图片时由单个任务显式提高。
    html_image_count: int = Field(default=5, ge=1, le=30)
    public_count: int = 1
    private_count: int = 0
    approval_mode: str = "auto"
    account_ids: Optional[List[int]] = None
    publish_mode: str = "draft"  # "draft" 存草稿箱, "direct" 直接发布
    image_source: str = "dashscope"  # 图片来源: dashscope/local
    footer_template: Optional[str] = None
    content_type: str = "article"  # article / image / video
    enabled_image_methods: Optional[List[str]] = None  # 配图方式
    enable_watermark: bool = False
    erp_image_config: Optional[ScheduledErpImageConfig] = None


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None
    feed_source_id: Optional[int] = None
    feed_article_ids: Optional[List[int]] = None
    style: Optional[str] = None
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: Optional[int] = None
    publish_times: Optional[List[str]] = None
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: Optional[int] = None
    html_image_count: Optional[int] = Field(default=None, ge=1, le=30)
    public_count: Optional[int] = None
    private_count: Optional[int] = None
    approval_mode: Optional[str] = None
    account_ids: Optional[List[int]] = None
    publish_mode: Optional[str] = None
    image_source: Optional[str] = None
    footer_template: Optional[str] = None
    content_type: Optional[str] = None
    enabled_image_methods: Optional[List[str]] = None
    enable_watermark: Optional[bool] = None
    erp_image_config: Optional[ScheduledErpImageConfig] = None


class SlotResponse(BaseModel):
    id: int
    sort_order: int
    content_type: str
    publish_domain: str

    model_config = {"from_attributes": True}


class ScheduledTaskResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    is_active: bool
    writing_mode: str
    topic: Optional[str] = None
    feed_source_ids: Optional[list] = None
    feed_source_id: Optional[int] = None
    feed_article_ids: Optional[list] = None
    style: Optional[str] = None
    knowledge_base_ids: Optional[list] = None
    day_of_week: int
    publish_times: list
    article_slots: Optional[list] = None  # legacy JSON field (read-only)
    slots: List[SlotResponse] = []  # new slot table records
    articles_per_day: int
    html_image_count: int = 5
    public_count: int
    private_count: int
    approval_mode: str
    account_ids: Optional[list] = None
    publish_mode: str = "draft"
    image_source: str = "dashscope"
    footer_template: Optional[str] = None
    content_type: str = "article"
    enabled_image_methods: Optional[list] = None
    enable_watermark: bool = False
    erp_image_config: Optional[dict] = None
    total_generated: int
    last_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduledTaskListResponse(BaseModel):
    total: int
    items: List[ScheduledTaskResponse]


def _load_task_slots(db: Session, task: ScheduledTask) -> list:
    """Load slots from ScheduledTaskSlot table, fall back to article_slots JSON."""
    slots = db.query(ScheduledTaskSlot).filter(
        ScheduledTaskSlot.task_id == task.id
    ).order_by(ScheduledTaskSlot.sort_order).all()
    if slots:
        return slots
    # Fallback: migrate legacy JSON slots
    if task.article_slots:
        for i, s in enumerate(task.article_slots):
            slot_data = s if isinstance(s, dict) else {}
            db.add(ScheduledTaskSlot(
                task_id=task.id,
                sort_order=i,
                content_type=slot_data.get("content_type", "image_text"),
                publish_domain=slot_data.get("publish_domain", "public"),
            ))
            slots.append(db.query(ScheduledTaskSlot).order_by(
                ScheduledTaskSlot.id.desc()).first())
        db.commit()
    return slots


# --- Routes ---

@router.get("/scheduled-tasks", response_model=ScheduledTaskListResponse)
def list_scheduled_tasks(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List all scheduled tasks with their slots."""
    items = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.tenant_id == principal.tenant_id)
        .order_by(ScheduledTask.id.desc())
        .all()
    )
    # Manually attach slots since Ticket doesn't use relationships
    result_items = []
    for item in items:
        resp = ScheduledTaskResponse.model_validate(item)
        resp.slots = _load_task_slots(db, item)
        result_items.append(resp)
    return ScheduledTaskListResponse(total=len(items), items=result_items)


@router.post("/scheduled-tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new scheduled task."""
    if req.day_of_week not in range(-1, 7):
        raise HTTPException(status_code=400, detail="day_of_week must be -1 (every day) or 0-6")

    task = ScheduledTask(
        tenant_id=principal.tenant_id,
        name=req.name,
        writing_mode="feed" if (req.feed_source_ids or req.feed_source_id) else "kb" if req.knowledge_base_ids else "free",
        feed_source_ids=req.feed_source_ids or ([req.feed_source_id] if req.feed_source_id else None),
        # 同时保存标量投喂源和具体文章选择，确保定时执行时复用用户明确选定的文章，
        # 不会因为只保存来源列表而退化为从投喂源随机挑选其他文章。
        feed_source_id=req.feed_source_id,
        feed_article_ids=req.feed_article_ids,
        topic=req.topic,
        style=req.style,
        knowledge_base_ids=req.knowledge_base_ids,
        day_of_week=req.day_of_week,
        publish_times=req.publish_times,
        article_slots=None,  # migrated to ScheduledTaskSlot table
        articles_per_day=req.articles_per_day,
        html_image_count=req.html_image_count,
        public_count=req.public_count,
        private_count=req.private_count,
        approval_mode=req.approval_mode,
        account_ids=req.account_ids,
        publish_mode=req.publish_mode,
        image_source=req.image_source,
        footer_template=req.footer_template,
        content_type=req.content_type,
        enabled_image_methods=req.enabled_image_methods,
        enable_watermark=req.enable_watermark,
        erp_image_config=req.erp_image_config.model_dump() if req.erp_image_config else None,
        created_by=principal.user_id,
    )
    db.add(task)
    db.flush()

    # Create slot records in the new table
    if req.article_slots:
        for i, slot in enumerate(req.article_slots):
            db.add(ScheduledTaskSlot(
                task_id=task.id,
                sort_order=i,
                content_type=slot.content_type,
                publish_domain=slot.publish_domain,
            ))

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
    # Handle article_slots: replace all slot records
    if "article_slots" in update_data:
        slots_data = update_data.pop("article_slots")
        if slots_data is not None:
            db.query(ScheduledTaskSlot).filter(
                ScheduledTaskSlot.task_id == task.id
            ).delete()
            for i, slot in enumerate(slots_data):
                slot_data = slot if isinstance(slot, dict) else slot.model_dump()
                db.add(ScheduledTaskSlot(
                    task_id=task.id,
                    sort_order=i,
                    content_type=slot_data.get("content_type", "image_text"),
                    publish_domain=slot_data.get("publish_domain", "public"),
                ))

    for field, value in update_data.items():
        if field == "erp_image_config" and value is not None:
            value = value.model_dump() if hasattr(value, "model_dump") else value
        setattr(task, field, value)

    # auto-derive writing_mode from the presence of feed/kb sources
    if "feed_source_ids" in update_data or "feed_source_id" in update_data or "knowledge_base_ids" in update_data:
        task.writing_mode = "feed" if (task.feed_source_ids or task.feed_source_id) else "kb" if task.knowledge_base_ids else "free"
    # 前端只传 feed_source_id 时不一定会更新 feed_source_ids，这里补上
    if "feed_source_id" in update_data and not task.feed_source_ids:
        task.feed_source_ids = [update_data["feed_source_id"]] if update_data["feed_source_id"] else None

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
