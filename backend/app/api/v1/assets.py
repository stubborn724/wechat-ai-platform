"""Asset library CRUD — file upload, storage, watermark, and serving via MinIO."""

import io
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, func
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import Asset
from app.services.storage_service import storage_service, generate_object_key

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class AssetResponse(BaseModel):
    id: int
    tenant_id: int
    filename: str
    original_filename: Optional[str] = None
    asset_type: str
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    storage_key: str
    thumbnail_key: Optional[str] = None
    tags: Optional[list] = None
    width: Optional[int] = None
    height: Optional[int] = None
    is_watermarked: bool
    watermark_config: Optional[dict] = None
    usage_count: int
    preview_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssetListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AssetResponse]


class WatermarkConfigRequest(BaseModel):
    type: str = "logo"  # logo or text
    image_key: Optional[str] = None  # for logo mode
    content: Optional[str] = None  # for text mode
    position: str = "bottom-right"
    opacity: float = 0.8
    scale: float = 0.15  # logo scale ratio
    font_size: int = 36
    color: str = "#FFFFFF"
    margin: int = 20


# --- Routes ---

@router.get("/assets", response_model=AssetListResponse)
def list_assets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    asset_type: Optional[str] = Query(None, alias="type"),
    tags: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List assets with pagination, filterable by type and tags."""
    query = db.query(Asset)

    if asset_type:
        query = query.filter(Asset.asset_type == asset_type)

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            if tag.startswith("-"):
                exclude_tag = tag[1:]
                query = query.filter(
                    cast(Asset.tags, String).notlike(f'%{exclude_tag}%')
                )
            else:
                query = query.filter(
                    cast(Asset.tags, String).like(f'%{tag}%')
                )

    total = query.count()
    items = query.order_by(Asset.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Enrich with preview URLs
    enriched = []
    for item in items:
        resp = AssetResponse.model_validate(item)
        resp.preview_url = storage_service.get_url(item.storage_key)
        enriched.append(resp)

    return AssetListResponse(total=total, page=page, page_size=page_size, items=enriched)


@router.post("/assets/upload", response_model=AssetResponse,
             status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(...),
    asset_type: str = Query("image"),
    tags: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Upload an asset file to MinIO storage.

    Supported types: image, video, document. The file is stored in MinIO
    and a metadata record is created in the database.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Filename is required")

    # Read file bytes
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Empty file")

    content_type = file.content_type or "application/octet-stream"

    # Generate storage key
    storage_key = generate_object_key(
        tenant_id=principal.tenant_id,
        filename=file.filename,
        prefix="assets",
    )

    # Determine image dimensions for image types
    width = None
    height = None
    if asset_type == "image" and content_type.startswith("image/"):
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(file_bytes))
            width, height = img.size
        except Exception:
            pass

    # Apply watermark automatically if tenant config enabled
    if asset_type == "image" and content_type.startswith("image/"):
        try:
            from app.models.mysql_models import TenantWatermarkConfig
            wm_config = db.query(TenantWatermarkConfig).filter(
                TenantWatermarkConfig.tenant_id == principal.tenant_id,
                TenantWatermarkConfig.enabled.is_(True),
            ).first()
            if wm_config and wm_config.enabled:
                from app.services.watermark_service import watermark_service
                wm_params = {
                    "type": wm_config.watermark_type,
                    "position": wm_config.position,
                    "opacity": wm_config.opacity / 100.0,
                    "margin": wm_config.margin,
                }
                if wm_config.watermark_type == "logo" and wm_config.logo_image_key:
                    wm_params["image_key"] = wm_config.logo_image_key
                    wm_params["scale"] = wm_config.scale / 100.0
                    file_bytes = watermark_service.apply_image_watermark(
                        file_bytes, wm_params, content_type,
                    )
                    logger.info("Auto-applied logo watermark on upload")
                elif wm_config.watermark_type == "text" and wm_config.text_content:
                    wm_params["content"] = wm_config.text_content
                    wm_params["font_size"] = wm_config.font_size
                    wm_params["color"] = wm_config.color
                    file_bytes = watermark_service.apply_image_watermark(
                        file_bytes, wm_params, content_type,
                    )
                    logger.info("Auto-applied text watermark on upload")
        except Exception as exc:
            logger.warning("Auto-watermark on upload failed: %s", exc)

    # Upload to MinIO
    try:
        storage_service.upload_bytes(
            object_name=storage_key,
            data=file_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        logger.error("MinIO upload failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Storage upload failed: {exc}")

    # Save database record
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    asset = Asset(
        tenant_id=principal.tenant_id,
        filename=storage_key.rsplit("/", 1)[-1],
        original_filename=file.filename,
        asset_type=asset_type,
        mime_type=content_type,
        file_size=len(file_bytes),
        storage_key=storage_key,
        tags=tag_list,
        width=width,
        height=height,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    logger.info("Asset uploaded: id=%d, key=%s, type=%s, size=%d",
                asset.id, storage_key, asset_type, len(file_bytes))

    resp = AssetResponse.model_validate(asset)
    resp.preview_url = storage_service.get_url(storage_key)
    return resp


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get asset detail."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    resp = AssetResponse.model_validate(asset)
    resp.preview_url = storage_service.get_url(asset.storage_key)
    return resp


@router.get("/assets/{asset_id}/file")
def get_asset_file(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Redirect to the actual file URL (MinIO presigned URL or public URL)."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    file_url = storage_service.get_url(asset.storage_key)
    return RedirectResponse(url=file_url)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete an asset (from database and MinIO storage)."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    # Delete from MinIO
    if asset.storage_key:
        storage_service.delete(asset.storage_key)

    # Delete thumbnail if exists
    if asset.thumbnail_key:
        storage_service.delete(asset.thumbnail_key)

    # Delete database record
    db.delete(asset)
    db.commit()
    logger.info("Asset deleted: id=%d, key=%s", asset_id, asset.storage_key)


# ============================================================================
# Watermark
# ============================================================================


@router.post("/assets/{asset_id}/watermark", response_model=AssetResponse)
async def apply_watermark(
    asset_id: int,
    config: WatermarkConfigRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """对素材图片叠加水印，生成新的水印版本"""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    if asset.asset_type not in ("image",):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Watermark only supported for images")

    # 下载原图
    try:
        image_data = storage_service.download_bytes(asset.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to read image: {exc}")

    # 叠加水印
    from app.services.watermark_service import watermark_service

    wm_config = config.model_dump(exclude_none=True)
    try:
        watermarked = watermark_service.apply_image_watermark(
            image_data, wm_config,
            content_type=asset.mime_type or "image/jpeg",
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Watermark failed: {exc}")

    # 上传水印版到 MinIO
    wm_key = asset.storage_key.rsplit(".", 1)
    wm_storage_key = f"{wm_key[0]}_watermarked.{wm_key[1]}" if len(wm_key) > 1 else f"{asset.storage_key}_watermarked"
    try:
        storage_service.upload_bytes(
            object_name=wm_storage_key,
            data=watermarked,
            content_type=asset.mime_type or "image/jpeg",
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Storage upload failed: {exc}")

    # 更新数据库记录
    asset.is_watermarked = True
    asset.watermark_config = wm_config
    asset.storage_key = wm_storage_key
    db.commit()
    db.refresh(asset)

    resp = AssetResponse.model_validate(asset)
    resp.preview_url = storage_service.get_url(asset.storage_key)
    return resp


@router.delete("/assets/{asset_id}/watermark", response_model=AssetResponse)
def remove_watermark(
    asset_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """去除水印，恢复到原始版本"""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if not asset.is_watermarked:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset is not watermarked")

    # 从水印 key 反推原始 key
    original_key = asset.storage_key.replace("_watermarked.", ".")
    # 检查原始文件是否存在
    if not storage_service.exists(original_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original file not found in storage")

    asset.is_watermarked = False
    asset.watermark_config = None
    asset.storage_key = original_key
    db.commit()
    db.refresh(asset)

    resp = AssetResponse.model_validate(asset)
    resp.preview_url = storage_service.get_url(asset.storage_key)
    return resp
