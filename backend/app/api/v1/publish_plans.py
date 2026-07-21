"""Publish plan CRUD"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import PublishPlan

router = APIRouter()


# --- Schemas ---

class PublishPlanCreate(BaseModel):
    account_id: int
    day_of_week: int  # 0=Monday, 6=Sunday
    article_slots: Optional[list] = None
    publish_times: Optional[list] = None
    public_count: int = 0
    private_count: int = 0


class PublishPlanUpdate(BaseModel):
    day_of_week: Optional[int] = None
    article_slots: Optional[list] = None
    publish_times: Optional[list] = None
    public_count: Optional[int] = None
    private_count: Optional[int] = None
    is_active: Optional[bool] = None


class PublishPlanResponse(BaseModel):
    id: int
    tenant_id: int
    account_id: int
    day_of_week: int
    article_slots: Optional[list] = None
    publish_times: Optional[list] = None
    public_count: int
    private_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PublishPlanListResponse(BaseModel):
    total: int
    items: List[PublishPlanResponse]


# --- Routes ---

@router.get("/publish-plans", response_model=PublishPlanListResponse)
def list_publish_plans(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List publish plans, optionally filtered by account."""
    query = db.query(PublishPlan)

    if account_id is not None:
        query = query.filter(PublishPlan.account_id == account_id)

    plans = query.order_by(PublishPlan.day_of_week.asc()).all()
    return PublishPlanListResponse(total=len(plans), items=plans)


@router.post("/publish-plans", response_model=PublishPlanResponse, status_code=status.HTTP_201_CREATED)
def create_publish_plan(
    req: PublishPlanCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new publish plan."""
    if req.day_of_week < 0 or req.day_of_week > 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="day_of_week must be between 0 (Monday) and 6 (Sunday)",
        )

    plan = PublishPlan(
        tenant_id=1,  # TODO: resolve from principal
        account_id=req.account_id,
        day_of_week=req.day_of_week,
        article_slots=req.article_slots,
        publish_times=req.publish_times,
        public_count=req.public_count,
        private_count=req.private_count,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.put("/publish-plans/{plan_id}", response_model=PublishPlanResponse)
def update_publish_plan(
    plan_id: int,
    req: PublishPlanUpdate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update a publish plan."""
    plan = db.query(PublishPlan).filter(PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish plan not found")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/publish-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_publish_plan(
    plan_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a publish plan."""
    plan = db.query(PublishPlan).filter(PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish plan not found")

    db.delete(plan)
    db.commit()
