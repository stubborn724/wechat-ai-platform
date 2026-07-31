"""仿写任务 API — 仿写池管理 + 仿写任务调度 + 结构分析触发"""
import logging
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ImitationPool, ImitationTask

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================


class PoolCreate(BaseModel):
    name: str
    description: str = ""


class PoolResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    source_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class AddSourceRequest(BaseModel):
    feed_source_id: Optional[int] = None
    wechat_name: Optional[str] = None
    wechat_app_id: Optional[str] = None
    weight: int = 1


class PoolSourceResponse(BaseModel):
    id: int
    feed_source_id: Optional[int] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    wechat_name: Optional[str] = None
    wechat_app_id: Optional[str] = None
    weight: int
    article_count: int = 0


class TaskCreate(BaseModel):
    name: str
    pool_id: int
    title: Optional[str] = None
    strategy: str = "random"
    articles_per_day: int = 1
    content_types: Optional[List[str]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    publish_times: Optional[List[str]] = None
    account_id: Optional[int] = None
    approval_mode: str = "auto"
    knowledge_base_ids: Optional[List[int]] = None
    footer_template: Optional[str] = None
    # 模式在 API 层收敛为两个稳定值，避免未知字符串进入任务执行后静默回退。
    imitation_mode: Literal["content", "html_layout"] = "content"


class TaskResponse(BaseModel):
    id: int
    name: str
    pool_id: Optional[int] = None
    strategy: str
    articles_per_day: int
    status: str
    total_generated: int
    imitation_mode: Literal["content", "html_layout"] = "content"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# 仿写池 CRUD
# ============================================================================


@router.get("/imitation/pools")
def list_pools(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出所有仿写池"""
    from app.services.imitation_service import list_pools as svc_list
    from app.models.mysql_models import ImitationPoolSource

    pools = svc_list(db, principal.tenant_id)
    result = []
    for p in pools:
        count = db.query(ImitationPoolSource).filter(
            ImitationPoolSource.pool_id == p.id,
            ImitationPoolSource.is_active == True,
        ).count()
        result.append(PoolResponse(
            id=p.id, name=p.name, description=p.description,
            is_active=p.is_active, source_count=count,
            created_at=p.created_at,
        ))
    return result


@router.post("/imitation/pools", status_code=status.HTTP_201_CREATED, response_model=PoolResponse)
def create_pool(
    req: PoolCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """创建仿写池"""
    from app.services.imitation_service import create_pool as svc_create
    pool = svc_create(db, principal.tenant_id, req.name, req.description)
    return PoolResponse(
        id=pool.id, name=pool.name, description=pool.description,
        is_active=pool.is_active, source_count=0, created_at=pool.created_at,
    )


@router.delete("/imitation/pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pool(
    pool_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """删除仿写池"""
    pool = db.query(ImitationPool).filter(
        ImitationPool.id == pool_id,
        ImitationPool.tenant_id == principal.tenant_id,
    ).first()
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    pool.is_active = False
    db.commit()


# ============================================================================
# 仿写池来源管理
# ============================================================================


@router.get("/imitation/pools/{pool_id}/sources")
def list_pool_sources(
    pool_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出仿写池中的来源"""
    pool = db.query(ImitationPool).filter(
        ImitationPool.id == pool_id,
        ImitationPool.tenant_id == principal.tenant_id,
    ).first()
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    from app.services.imitation_service import list_pool_sources as svc_list
    sources = svc_list(db, pool_id)
    return [PoolSourceResponse(**s) for s in sources]


@router.post("/imitation/pools/{pool_id}/sources", status_code=status.HTTP_201_CREATED)
def add_pool_source(
    pool_id: int,
    req: AddSourceRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """添加来源到仿写池"""
    from app.services.imitation_service import add_source_to_pool

    # 验证仿写池存在
    pool = db.query(ImitationPool).filter(
        ImitationPool.id == pool_id,
        ImitationPool.tenant_id == principal.tenant_id,
    ).first()
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")

    source = add_source_to_pool(
        db, pool_id,
        feed_source_id=req.feed_source_id,
        wechat_name=req.wechat_name,
        wechat_app_id=req.wechat_app_id,
        weight=req.weight,
    )
    return {"id": source.id, "pool_id": pool_id, "status": "added"}


@router.delete("/imitation/pools/{pool_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_pool_source(
    pool_id: int,
    source_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """从仿写池移除来源"""
    pool = db.query(ImitationPool).filter(
        ImitationPool.id == pool_id,
        ImitationPool.tenant_id == principal.tenant_id,
    ).first()
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    from app.services.imitation_service import remove_source_from_pool
    if not remove_source_from_pool(db, source_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")


@router.post("/imitation/pools/{pool_id}/analyze")
def analyze_pool(
    pool_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """对仿写池所有来源执行结构深度分析"""
    import asyncio
    from app.services.imitation_service import analyze_pool_sources

    # Verify pool ownership (the analyze function will check pool_id)
    pool = db.query(ImitationPool).filter(
        ImitationPool.id == pool_id,
        ImitationPool.tenant_id == principal.tenant_id,
    ).first()
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pool not found")
    results = analyze_pool_sources(db, pool_id)
    return {"pool_id": pool_id, "results": results}


# ============================================================================
# 仿写任务 CRUD
# ============================================================================


@router.get("/imitation/tasks")
def list_tasks(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出所有仿写任务"""
    from app.services.imitation_service import list_imitation_tasks as svc_list
    tasks = svc_list(db, principal.tenant_id)
    return [TaskResponse.model_validate(t) for t in tasks]


@router.post("/imitation/tasks", status_code=status.HTTP_201_CREATED, response_model=TaskResponse)
def create_task(
    req: TaskCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """创建仿写任务"""
    from app.services.imitation_service import create_imitation_task as svc_create

    task = svc_create(
        db=db,
        tenant_id=principal.tenant_id,
        name=req.name,
        title=req.title,
        pool_id=req.pool_id,
        strategy=req.strategy,
        articles_per_day=req.articles_per_day,
        content_types=req.content_types,
        start_date=req.start_date,
        end_date=req.end_date,
        publish_times=req.publish_times,
        account_id=req.account_id,
        approval_mode=req.approval_mode,
        knowledge_base_ids=req.knowledge_base_ids,
        footer_template=req.footer_template,
        imitation_mode=req.imitation_mode,
        created_by=principal.user_id,
    )
    return TaskResponse.model_validate(task)


@router.post("/imitation/tasks/{task_id}/execute")
async def execute_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """手动立即执行一次仿写任务"""
    from app.services.imitation_service import execute_imitation_task

    task = db.query(ImitationTask).filter(
        ImitationTask.id == task_id,
        ImitationTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    result = await execute_imitation_task(db, task_id)
    return result


@router.post("/imitation/tasks/{task_id}/toggle")
def toggle_task(
    task_id: int,
    action: str = Query("pause", regex="^(pause|resume)$"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """暂停/恢复仿写任务"""
    task = db.query(ImitationTask).filter(
        ImitationTask.id == task_id,
        ImitationTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if action == "pause":
        task.status = "paused"
    else:
        task.status = "active"
    db.commit()
    return {"id": task.id, "status": task.status}


@router.delete("/imitation/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """删除仿写任务"""
    task = db.query(ImitationTask).filter(
        ImitationTask.id == task_id,
        ImitationTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.status = "completed"
    db.commit()
