"""Tenant-level watermark configuration API — global defaults for watermarking."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import TenantWatermarkConfig
from app.services.storage_service import storage_service, generate_object_key

logger = logging.getLogger(__name__)
router = APIRouter()


class WatermarkConfigResponse(BaseModel):
    enabled: bool
    watermark_type: str
    logo_image_key: Optional[str] = None
    logo_url: Optional[str] = None
    scale: int
    text_content: Optional[str] = None
    font_size: int
    position: str
    opacity: int
    color: str
    margin: int
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UpdateWatermarkConfigRequest(BaseModel):
    enabled: bool = False
    watermark_type: str = "logo"
    logo_image_key: Optional[str] = None
    scale: int = 15
    text_content: Optional[str] = None
    font_size: int = 36
    position: str = "bottom-right"
    opacity: int = 80
    color: str = "#FFFFFF"
    margin: int = 20


def _get_or_create_config(db: Session, tenant_id: int) -> TenantWatermarkConfig:
    """Get the tenant's watermark config, creating a default one if none exists."""
    config = db.query(TenantWatermarkConfig).filter(
        TenantWatermarkConfig.tenant_id == tenant_id
    ).first()
    if not config:
        config = TenantWatermarkConfig(tenant_id=tenant_id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("/watermark-config", response_model=WatermarkConfigResponse)
def get_watermark_config(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get tenant watermark configuration."""
    config = _get_or_create_config(db, principal.tenant_id)
    return config


@router.put("/watermark-config", response_model=WatermarkConfigResponse)
def update_watermark_config(
    req: UpdateWatermarkConfigRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update tenant watermark configuration."""
    config = _get_or_create_config(db, principal.tenant_id)

    config.enabled = req.enabled
    config.watermark_type = req.watermark_type
    config.scale = req.scale
    config.font_size = req.font_size
    config.position = req.position
    config.opacity = req.opacity
    config.color = req.color
    config.margin = req.margin

    if req.watermark_type == "logo":
        if req.logo_image_key:
            config.logo_image_key = req.logo_image_key
            config.logo_url = storage_service.get_url(req.logo_image_key)
        config.text_content = None
    else:
        config.text_content = req.text_content
        config.logo_image_key = None
        config.logo_url = None

    db.commit()
    db.refresh(config)
    logger.info("Watermark config updated for tenant %d", principal.tenant_id)
    return config


@router.post("/watermark-config/upload-logo", response_model=dict)
async def upload_watermark_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Upload a logo image for watermark use.

    The image is stored in MinIO under ``watermark-logos/`` and the
    returned ``image_key`` / ``url`` can be saved to the watermark config.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    content_type = file.content_type or "image/png"
    storage_key = generate_object_key(
        tenant_id=principal.tenant_id,
        filename=file.filename,
        prefix="watermark-logos",
    )

    try:
        storage_service.upload_bytes(
            object_name=storage_key,
            data=file_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upload failed: {exc}",
        )

    url = storage_service.get_url(storage_key)
    logger.info("Watermark logo uploaded: key=%s", storage_key)
    return {"image_key": storage_key, "url": url}


class PreviewWatermarkRequest(BaseModel):
    """Preview watermark with these config values (no need to save first)."""
    asset_id: int
    watermark_type: str = "logo"
    logo_image_key: Optional[str] = None
    text_content: Optional[str] = None
    scale: int = 15
    font_size: int = 36
    position: str = "bottom-right"
    opacity: int = 80
    color: str = "#FFFFFF"
    margin: int = 20


@router.post("/watermark-config/preview", response_model=dict)
def preview_watermark_asset(
    req: PreviewWatermarkRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Preview watermark effect using the provided config (no need to save first).

    Selects an asset from the library and renders the watermark on it,
    returning a temporary preview URL.
    """
    from app.models.mysql_models import Asset

    asset = db.query(Asset).filter(
        Asset.id == req.asset_id,
        Asset.tenant_id == principal.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    # Validate config
    if req.watermark_type == "logo" and not req.logo_image_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先上传 Logo 图片再预览",
        )
    if req.watermark_type == "text" and not req.text_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先输入水印文字再预览",
        )

    # Download original
    try:
        image_data = storage_service.download_bytes(asset.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to read image: {exc}")

    # Build watermark config from request body
    wm_cfg: dict = {
        "type": req.watermark_type,
        "position": req.position,
        "opacity": req.opacity / 100.0,
        "margin": req.margin,
    }
    if req.watermark_type == "logo":
        wm_cfg["image_key"] = req.logo_image_key
        wm_cfg["scale"] = req.scale / 100.0
    else:
        wm_cfg["content"] = req.text_content or ""
        wm_cfg["font_size"] = req.font_size
        wm_cfg["color"] = req.color

    from app.services.watermark_service import watermark_service

    try:
        watermarked = watermark_service.apply_image_watermark(
            image_data, wm_cfg,
            content_type=asset.mime_type or "image/jpeg",
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Watermark failed: {exc}")

    # Save as temporary preview
    preview_key = f"previews/{principal.tenant_id}/watermark_preview_{asset.id}.jpg"
    try:
        storage_service.upload_bytes(
            object_name=preview_key,
            data=watermarked,
            content_type="image/jpeg",
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Upload failed: {exc}")

    preview_url = storage_service.get_url(preview_key)
    return {"preview_url": preview_url}
