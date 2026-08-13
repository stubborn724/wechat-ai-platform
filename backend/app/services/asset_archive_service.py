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
    target_size: tuple[int, int] | None = None,
    watermark_font_size: int | None = None,
    task_watermark_config: Optional[dict] = None,
    poster_copy: str | None = None,
    poster_kind: str = "content",
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
) -> Optional[Asset]:
    """Download an image URL and save it to the asset library.

    ``watermark_enabled`` overrides the tenant global config per-image.
    ``None`` = fallback to tenant config; ``True``/``False`` = force on/off.
    ``task_watermark_config`` 非空时代表任务快照，优先级高于全局配置；快照为空
    才查询租户水印表，避免任务固定样式被后续全局修改覆盖。
    """
    if not image_url and not image_bytes:
        return None

    try:
        if image_bytes is not None:
            # 连续海报切片已经由母版合成器在内存中产出，直接进入同一归档后处理
            # 管线，避免再次请求相同主视觉 URL或让上游地址短时失效。
            file_bytes = image_bytes
            content_type = image_content_type or "image/jpeg"
        else:
            validate_url(image_url)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            file_bytes = resp.content

        # 归一化会把定时图片编码为 PNG/JPEG/WEBP 之一，因此扩展名必须在归一化
        # 后再确定；否则供应商返回 JPG、程序输出 PNG 时对象键和真实格式会不一致。
        ext = ".jpg"

        # 仅定时 ERP 归档传入 target_size。供应商返回尺寸不稳定时先归一化，再
        # 执行全局水印和文章署名，保证水印的像素比例基于最终画布而非原图。
        if target_size is not None:
            from app.services.scheduled_image_normalization_service import (
                normalize_scheduled_image_bytes,
            )

            normalized = normalize_scheduled_image_bytes(
                file_bytes,
                content_type=content_type,
                target_size=target_size,
            )
            file_bytes = normalized.data
            content_type = normalized.content_type
            width, height = normalized.size
        else:
            width, height = None, None

        # 普通文章不传 target_size，保留其原始尺寸；定时 ERP 图片已经在上面
        # 得到目标尺寸，不能再次清空元数据。
        if target_size is None:
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(file_bytes))
                width, height = img.size
            except Exception:
                pass

        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"

        # 新三品牌的通用海报模板把文案放在 img 属性中，归档时才进行一次像素级
        # 合成。模型因此只需要关注真实产品和背景，且相同图片在重试/发布时不会
        # 因中文识别偏差出现错字。没有 poster_copy 的旧调用完全跳过该分支。
        if poster_copy:
            from app.services.poster_image_compositor import apply_poster_text_overlay

            file_bytes = apply_poster_text_overlay(
                file_bytes,
                copy=poster_copy,
                kind=poster_kind,
                content_type=content_type,
            )

        # 任务快照必须先规范化，再决定是否读取全局配置。非空快照即使关闭水印，
        # 也不能回退到全局开启状态；这正是任务级隔离与普通文章兼容逻辑的边界。
        from app.services.scheduled_task_watermark_service import (
            normalize_task_watermark_config,
        )

        normalized_task_watermark_config = normalize_task_watermark_config(
            task_watermark_config
        )
        wm_config = None
        if normalized_task_watermark_config is None:
            wm_config = db.query(TenantWatermarkConfig).filter(
                TenantWatermarkConfig.tenant_id == tenant_id,
            ).first()

        if normalized_task_watermark_config is not None:
            should_wm = bool(normalized_task_watermark_config["enabled"])
            if not should_wm or normalized_task_watermark_config["type"] != "text":
                # Logo 快照和关闭快照都不允许再叠加文章动态署名，否则一张图会出现
                # 两套水印；只有启用的文字快照需要继续走统一中文字体绘制器。
                article_image_attribution = None
            if should_wm and normalized_task_watermark_config["type"] == "logo":
                try:
                    file_bytes = watermark_service.apply_image_watermark(
                        file_bytes,
                        {
                            "type": "logo",
                            "image_key": normalized_task_watermark_config["image_key"],
                            "position": normalized_task_watermark_config["position"],
                            "opacity": normalized_task_watermark_config["opacity"],
                            "margin": normalized_task_watermark_config["margin"],
                            "scale": normalized_task_watermark_config["scale"],
                        },
                        content_type,
                        required=True,
                    )
                    logger.info("Applied task snapshot logo watermark to archived image")
                except Exception as exc:
                    # 任务快照是发布时已经确认过的品牌要求。此处继续上传原图会让
                    # 后续微信发布绕过 Logo，因此必须失败并交给定时任务重试。
                    raise RuntimeError("任务 Logo 水印绘制失败，已阻止归档") from exc
            elif should_wm and article_image_attribution is None:
                # 直接调用归档服务时可能没有经过文章收口服务，仍然保证文字快照
                # 不会因为调用方遗漏 attribution 而静默丢失。
                from app.services.article_publication_polish_service import (
                    ArticleImageAttribution,
                )

                article_image_attribution = ArticleImageAttribution(
                    lines=(normalized_task_watermark_config["content"],)
                )
                watermark_font_size = watermark_font_size or normalized_task_watermark_config[
                    "font_size"
                ]
        else:
            should_wm = (
                watermark_enabled
                if watermark_enabled is not None
                else (wm_config.enabled if wm_config else False)
            )
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
                    font_size=watermark_font_size,
                )
                logger.info("Applied dynamic article image attribution")
            except Exception as exc:
                if (
                    normalized_task_watermark_config is not None
                    and should_wm
                    and normalized_task_watermark_config["type"] == "text"
                ):
                    # 定时任务的文字水印同样是硬性发布条件。仅记录 warning 会把
                    # 无品牌标识的图片写入素材库并继续发布，必须在上传前中断。
                    raise RuntimeError("任务文字水印绘制失败，已阻止归档") from exc
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
    target_size: tuple[int, int] | None = None,
    watermark_font_size: int | None = None,
    task_watermark_config: Optional[dict] = None,
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
            target_size=target_size,
            watermark_font_size=watermark_font_size,
            task_watermark_config=task_watermark_config,
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
