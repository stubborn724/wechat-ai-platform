"""内容素材 API — 纯图片/视频的生成素材管理"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, computed_field
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import ContentAsset
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)
router = APIRouter()


class ContentAssetResponse(BaseModel):
    id: int
    tenant_id: int
    job_id: int
    content_type: str
    asset_type: str
    storage_key: str
    file_format: Optional[str] = None
    file_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_sec: Optional[int] = None
    sort_order: int = 0
    version: int = 1
    phase: str = "pending"
    error_message: Optional[str] = None
    generation_config: Optional[dict] = None
    parent_asset_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def file_url(self) -> str:
        return storage_service.get_url(self.storage_key) if self.storage_key else ""


class ContentAssetListResponse(BaseModel):
    total: int
    items: List[ContentAssetResponse]


@router.get("/content-assets", response_model=ContentAssetListResponse)
def list_content_assets(
    job_id: Optional[int] = Query(None),
    content_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """查询内容素材列表（按租户隔离）"""
    query = db.query(ContentAsset).filter(
        ContentAsset.tenant_id == principal.tenant_id,
    )
    if job_id:
        query = query.filter(ContentAsset.job_id == job_id)
    if content_type:
        query = query.filter(ContentAsset.content_type == content_type)
    if asset_type:
        query = query.filter(ContentAsset.asset_type == asset_type)

    total = query.count()
    items = (
        query.order_by(ContentAsset.sort_order.asc(), ContentAsset.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ContentAssetListResponse(total=total, items=items)


@router.get("/content-assets/{asset_id}", response_model=ContentAssetResponse)
def get_content_asset(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取素材详情"""
    asset = db.query(ContentAsset).filter(
        ContentAsset.id == asset_id,
        ContentAsset.tenant_id == principal.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content asset not found")
    return asset


@router.get("/content-assets/{asset_id}/file")
def get_content_asset_file(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """重定向到素材文件（MinIO URL）"""
    asset = db.query(ContentAsset).filter(
        ContentAsset.id == asset_id,
        ContentAsset.tenant_id == principal.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content asset not found")

    file_url = storage_service.get_url(asset.storage_key)
    return RedirectResponse(url=file_url)


@router.post("/content-assets/{asset_id}/regenerate")
def regenerate_content_asset(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """重新生成单个素材（标记为 pending，由后台任务处理）"""
    asset = db.query(ContentAsset).filter(
        ContentAsset.id == asset_id,
        ContentAsset.tenant_id == principal.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content asset not found")

    asset.phase = "pending"
    asset.error_message = None
    asset.version += 1
    db.commit()

    # 触发后台重试
    try:
        from app.tasks.content_tasks import process_image_job, process_video_job
        if asset.content_type in ("image", "pure_image"):
            process_image_job.delay(asset.job_id)
        elif asset.content_type == "video":
            process_video_job.delay(asset.job_id)
    except Exception as exc:
        logger.warning("Failed to dispatch regeneration for asset %d: %s", asset_id, exc)

    return {"message": "Regeneration triggered", "asset_id": asset_id}


@router.delete("/content-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content_asset(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """删除素材（从 MinIO 和数据库）"""
    asset = db.query(ContentAsset).filter(
        ContentAsset.id == asset_id,
        ContentAsset.tenant_id == principal.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content asset not found")

    if asset.storage_key:
        try:
            storage_service.delete(asset.storage_key)
        except Exception as exc:
            logger.warning("Failed to delete storage %s: %s", asset.storage_key, exc)

    db.delete(asset)
    db.commit()
