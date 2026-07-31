"""Asset archive service — usage tracking, auto-archive, and library saving."""

import logging
import re
from datetime import datetime
from typing import Any, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import Asset, AssetUsage, TenantWatermarkConfig
from app.services.storage_service import generate_object_key, storage_service
from app.services.url_safety import validate_url
from app.services.watermark_service import watermark_service

logger = logging.getLogger(__name__)


def build_archive_filename(name_hint: Optional[str], extension: str) -> str:
    """生成素材库可读的原始文件名，并隔离不允许出现在文件名中的字符。

    对象存储键由 UUID 保证唯一性，显示名无需拼接时间戳。这样 ERP 产品导入后，
    用户看到的是产品名称，而不是自动归档过程产生的技术文件名。
    """
    fallback_name = f"auto_archive_{int(datetime.now().timestamp())}"
    raw_name = (name_hint or fallback_name).strip()
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).rstrip(". ")
    safe_name = safe_name or fallback_name
    normalized_extension = extension if extension.startswith(".") else f".{extension}"
    if safe_name.lower().endswith(normalized_extension.lower()):
        return safe_name[:255]
    return f"{safe_name[:255 - len(normalized_extension)]}{normalized_extension}"


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
    watermark_enabled: Optional[bool] = None,
    original_filename: Optional[str] = None,
    article_image_attribution: Optional[Any] = None,
) -> Optional[Asset]:
    """Download an image URL and save it to the asset library.

    ``watermark_enabled`` overrides the tenant global config per-image.
    ``None`` = fallback to tenant config; ``True``/``False`` = force on/off.
    """
    if not image_url:
        return None

    try:
        validate_url(image_url)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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

        # Apply watermark (per-request override or tenant global config)
        wm_config = db.query(TenantWatermarkConfig).filter(
            TenantWatermarkConfig.tenant_id == tenant_id,
        ).first()
        should_wm = watermark_enabled if watermark_enabled is not None else (wm_config.enabled if wm_config else False)
        if should_wm and wm_config:
            try:
                wm_params = {
                    "type": wm_config.watermark_type,
                    "position": wm_config.position,
                    "opacity": wm_config.opacity / 100.0,
                    "margin": wm_config.margin,
                }
                if wm_config.watermark_type == "logo":
                    if wm_config.logo_image_key:
                        wm_params["image_key"] = wm_config.logo_image_key
                        wm_params["scale"] = wm_config.scale / 100.0
                        file_bytes = watermark_service.apply_image_watermark(
                            file_bytes, wm_params, content_type,
                        )
                        logger.info("Applied logo watermark to archived image")
                else:
                    wm_params["content"] = wm_config.text_content or ""
                    wm_params["font_size"] = wm_config.font_size
                    wm_params["color"] = wm_config.color
                    if wm_params["content"]:
                        file_bytes = watermark_service.apply_image_watermark(
                            file_bytes, wm_params, content_type,
                        )
                        logger.info("Applied text watermark to archived image")
            except Exception as exc:
                logger.warning("Failed to apply watermark on archive: %s", exc)

        # 文章图片必须在归档地址上叠加当前产品和品牌联系方式。该步骤位于租户
        # 全局水印之后，确保动态产品名不会被后续 Logo 覆盖；普通素材库导入不传
        # ``article_image_attribution``，继续保持原有的全局水印行为。
        if article_image_attribution is not None:
            try:
                from app.services.article_publication_polish_service import (
                    apply_article_image_attribution_to_bytes,
                )

                file_bytes = apply_article_image_attribution_to_bytes(
                    file_bytes,
                    attribution=article_image_attribution,
                    content_type=content_type,
                )
                logger.info("Applied dynamic article image attribution")
            except Exception as exc:
                logger.warning("Failed to apply article image attribution: %s", exc)

        # Upload to MinIO
        # 业务调用方可以提供产品名作为展示文件名；存储键仍使用 UUID，防止重名覆盖。
        filename = build_archive_filename(original_filename, ext)
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
    watermark_enabled: Optional[bool] = None,
    article_image_attribution: Optional[Any] = None,
) -> List[Asset]:
    """Save multiple image URLs to the asset library."""
    assets = []
    for i, url in enumerate(image_urls):
        kw = keywords[i] if keywords and i < len(keywords) else ""
        asset = await save_image_to_asset_library(
            db,
            tenant_id,
            url,
            kw,
            watermark_enabled=watermark_enabled,
            article_image_attribution=article_image_attribution,
        )
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
