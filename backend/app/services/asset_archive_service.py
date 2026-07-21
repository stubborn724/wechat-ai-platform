"""Asset archive service — usage tracking, auto-archive, and library saving."""

import logging
from datetime import datetime
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import Asset, AssetUsage
from app.services.storage_service import generate_object_key, storage_service

logger = logging.getLogger(__name__)


def record_asset_usage(
    db: Session,
    asset_id: int,
    content_version_id: Optional[int] = None,
    usage_type: str = "article_image",
) -> AssetUsage:
    """Create an AssetUsage record and increment usage_count."""
    usage = AssetUsage(
        asset_id=asset_id,
        content_version_id=content_version_id,
        usage_type=usage_type,
    )
    db.add(usage)

    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset:
        asset.usage_count = (asset.usage_count or 0) + 1

    db.commit()
    return usage


async def save_image_to_asset_library(
    db: Session,
    tenant_id: int,
    image_url: str,
    keywords: str = "",
    usage_type: str = "generated_image",
) -> Optional[Asset]:
    """Download an image URL and save it to the asset library.

    Downloads the image, uploads to MinIO under ``assets/auto/``,
    creates an Asset record, and returns it.
    """
    if not image_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "image/jpeg")
        file_bytes = resp.content

        # Determine extension
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"

        # Determine dimensions
        width, height = None, None
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(file_bytes))
            width, height = img.size
        except Exception:
            pass

        # Upload to MinIO
        filename = f"auto_archive_{int(datetime.now().timestamp())}{ext}"
        storage_key = generate_object_key(tenant_id, filename, prefix="assets/auto")
        storage_service.upload_bytes(
            object_name=storage_key,
            data=file_bytes,
            content_type=content_type,
        )

        # Create asset record
        tag_list = [keywords] if keywords else ["auto-archived"]
        asset = Asset(
            tenant_id=tenant_id,
            filename=storage_key.rsplit("/", 1)[-1],
            original_filename=filename,
            asset_type="image",
            mime_type=content_type,
            file_size=len(file_bytes),
            storage_key=storage_key,
            tags=tag_list,
            width=width,
            height=height,
        )
        db.add(asset)
        db.flush()

        db.commit()
        logger.info("Saved generated image to asset library: id=%d, key=%s",
                    asset.id, storage_key)
        return asset

    except Exception as exc:
        logger.warning("Failed to save image to asset library: %s", exc)
        return None


async def save_images_to_asset_library(
    db: Session,
    tenant_id: int,
    image_urls: List[str],
    keywords: Optional[List[str]] = None,
) -> List[Asset]:
    """Save multiple image URLs to the asset library."""
    assets = []
    for i, url in enumerate(image_urls):
        kw = keywords[i] if keywords and i < len(keywords) else ""
        asset = await save_image_to_asset_library(db, tenant_id, url, kw)
        if asset:
            assets.append(asset)
    return assets


def get_archive_candidates(
    db: Session,
    max_days_unused: int = 90,
    limit: int = 50,
) -> List[Asset]:
    """Find assets eligible for archival: no usage and older than max_days."""
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days_unused)
    return (
        db.query(Asset)
        .filter(
            Asset.created_at < cutoff,
            Asset.usage_count == 0,
        )
        .limit(limit)
        .all()
    )
