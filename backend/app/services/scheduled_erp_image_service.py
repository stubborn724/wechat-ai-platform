"""定时任务的 ERP 分类选图与本地归档服务。"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import cast, String
from sqlalchemy.orm import Session

from app.models.mysql_models import Asset, ScheduledTaskErpImageUsage
from app.services.asset_archive_service import build_archive_filename, save_image_to_asset_library
from app.services.cos_image_relay_service import CosImageRelayService
from app.services.erp_product_service import (
    ErpProduct,
    ErpProductApiError,
    build_erp_product_client_from_settings,
)
from app.services.scheduled_erp_image_policy import (
    ErpImageSelectionError,
    select_unused_erp_products,
)
from app.services.storage_service import storage_service
from app.services.wanxiang_reference_image_service import normalize_reference_image


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledErpImageConfig:
    """ERP 配图的已校验配置。

    ``commodity_category`` 仅是筛选项。省略它时，任务会在指定 ERP 来源的全部
    产品中随机选图，适用于按品牌/产品线轮换的定时任务。
    """

    source_key: str
    commodity_category: Optional[str] = None
    repeat_after_days: int = 3
    image_count: int = 8


@dataclass(frozen=True)
class PreparedErpImage:
    """已归档并完成临时公网中转的 ERP 图片。

    ``local_url`` 只供本地素材库展示；``reference_url`` 是万相在签名有效期内
    可访问的 COS HTTPS 地址；``relay_object_key`` 交给任务编排层在 finally 中
    精确清理，避免把短期中转对象变成第二套长期素材库。
    """

    product: ErpProduct
    asset_id: int
    local_url: str
    reference_url: str
    relay_object_key: str
    reference_image_bytes: bytes
    reference_content_type: str


def parse_scheduled_erp_image_config(raw_config: object) -> ScheduledErpImageConfig | None:
    """解析旧任务可为空的新 ERP 配图配置，并对关键边界做严格校验。"""
    if not raw_config:
        return None
    if not isinstance(raw_config, dict):
        raise ErpImageSelectionError("ERP 图片配置格式错误")

    source_key = str(raw_config.get("source_key") or "").strip()
    category = str(raw_config.get("commodity_category") or "").strip() or None
    repeat_after_days = int(raw_config.get("repeat_after_days") or 3)
    image_count = int(raw_config.get("image_count") or 8)
    if not source_key:
        raise ErpImageSelectionError("ERP 图片配置必须指定来源")
    if not 1 <= repeat_after_days <= 30:
        raise ErpImageSelectionError("ERP 图片防重天数必须在 1 到 30 天之间")
    if not 1 <= image_count <= 20:
        raise ErpImageSelectionError("每篇文章的 ERP 图片数量必须在 1 到 20 张之间")
    return ScheduledErpImageConfig(
        source_key=source_key,
        commodity_category=category,
        repeat_after_days=repeat_after_days,
        image_count=image_count,
    )


async def prepare_erp_images_for_scheduled_run(
    db: Session,
    task_id: int,
    tenant_id: int,
    run_id: int,
    config: ScheduledErpImageConfig,
    requested_count: int,
    relay_service: CosImageRelayService | None = None,
) -> list[PreparedErpImage]:
    """挑选 ERP 图片、归档 MinIO，再中转 COS 并写入防重记录。

    中转读取已归档对象字节，不再次访问 ERP 外链，从而保证素材库保存的原图和
    万相看到的参考图完全一致。若准备过程中途失败，本方法会回收本次已经上传的
    COS 对象；全部成功后的正常清理由任务编排层在文章槽位 ``finally`` 中完成。
    """
    count = max(1, min(requested_count, config.image_count))
    recent_urls = _recent_erp_image_urls(db, task_id, config.repeat_after_days)
    products = await _load_category_products(config, recent_urls, count)
    relay = relay_service or CosImageRelayService()
    prepared_images: list[PreparedErpImage] = []
    staged_object_keys: list[str] = []

    try:
        for product in products:
            asset, local_url = await _import_product_image(
                db=db,
                tenant_id=tenant_id,
                source_key=config.source_key,
                product=product,
            )
            image_bytes = storage_service.download_bytes(asset.storage_key)
            normalized_image = normalize_reference_image(
                image_bytes,
                asset.mime_type or "image/jpeg",
            )
            relay_object = relay.stage_bytes(
                data=normalized_image.data,
                content_type=normalized_image.content_type,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            staged_object_keys.append(relay_object.object_key)
            prepared_images.append(PreparedErpImage(
                product=product,
                asset_id=asset.id,
                local_url=local_url,
                reference_url=relay_object.signed_url,
                relay_object_key=relay_object.object_key,
                reference_image_bytes=normalized_image.data,
                reference_content_type=normalized_image.content_type,
            ))
            db.add(ScheduledTaskErpImageUsage(
                task_id=task_id,
                run_id=run_id,
                asset_id=asset.id,
                erp_image_url=product.image_url,
                product_name=product.name,
            ))
    except Exception:
        # 调用方尚未拿到准备结果时无法负责清理，因此由本服务回收部分成功对象。
        for object_key in reversed(staged_object_keys):
            try:
                relay.delete_object(object_key)
            except Exception as cleanup_error:
                logger.warning("清理未完成 ERP 图片中转对象失败 key=%s: %s", object_key, cleanup_error)
        raise

    db.flush()
    return prepared_images


def _recent_erp_image_urls(db: Session, task_id: int, repeat_after_days: int) -> set[str]:
    """查询窗口期已用 ERP 图片，防重范围仅限当前定时任务。"""
    cutoff = datetime.utcnow() - timedelta(days=repeat_after_days)
    rows = (
        db.query(ScheduledTaskErpImageUsage.erp_image_url)
        .filter(
            ScheduledTaskErpImageUsage.task_id == task_id,
            ScheduledTaskErpImageUsage.used_at >= cutoff,
        )
        .all()
    )
    return {str(row[0]).strip() for row in rows if row[0]}


async def _load_category_products(
    config: ScheduledErpImageConfig,
    recent_urls: set[str],
    requested_count: int,
) -> list[ErpProduct]:
    """随机读取 ERP 来源的可选分类页，直到找到足够的未重复图片或明确报出素材不足。"""
    client = build_erp_product_client_from_settings()
    category_label = config.commodity_category or "全部分类"
    search_filters = {"pageSize": 50}
    if config.commodity_category:
        search_filters["commodityCategory"] = config.commodity_category
    initial_page = await client.search_products(
        config.source_key,
        {"pageNo": 1, **search_filters},
    )
    if initial_page.total < requested_count:
        raise ErpImageSelectionError(
            f"ERP “{category_label}”仅有 {initial_page.total} 张可用产品图，无法满足 {requested_count} 张配图"
        )

    pages = [initial_page]
    total_pages = max(1, math.ceil(initial_page.total / initial_page.page_size))
    page_numbers = list(range(2, total_pages + 1))
    random.SystemRandom().shuffle(page_numbers)

    # 多取若干随机页，兼顾大类随机性与调用成本；不足时再扫描剩余页保证正确性。
    for page_number in page_numbers[:4]:
        pages.append(await client.search_products(
            config.source_key,
            {"pageNo": page_number, **search_filters},
        ))

    candidates = [product for page in pages for product in page.products]
    try:
        return select_unused_erp_products(candidates, recent_urls, requested_count)
    except ErpImageSelectionError:
        for page_number in page_numbers[4:]:
            page = await client.search_products(
                config.source_key,
                {"pageNo": page_number, **search_filters},
            )
            candidates.extend(page.products)
            try:
                return select_unused_erp_products(candidates, recent_urls, requested_count)
            except ErpImageSelectionError:
                continue
        raise


async def _import_product_image(
    db: Session,
    tenant_id: int,
    source_key: str,
    product: ErpProduct,
) -> tuple[Asset, str]:
    """复用已有归档图片，或导入素材库后返回资产实体和本地展示 URL。

    返回完整 ``Asset`` 是为了让调用方通过稳定的 ``storage_key`` 读取归档字节，
    避免根据公开 URL 反向解析对象键，保持存储层边界清晰。
    """
    source = build_erp_product_client_from_settings().get_source(source_key)
    marker = f"erp-image:{hashlib.sha256(product.image_url.encode('utf-8')).hexdigest()[:24]}"
    existing = (
        db.query(Asset)
        .filter(
            Asset.tenant_id == tenant_id,
            cast(Asset.tags, String).like(f"%{marker}%"),
        )
        .first()
    )
    if existing:
        return existing, storage_service.get_url(existing.storage_key)

    tags = ",".join(dict.fromkeys(["ERP产品", source.name, product.name, marker, *product.tags]))
    asset = await save_image_to_asset_library(
        db=db,
        tenant_id=tenant_id,
        image_url=product.image_url,
        keywords=tags,
        usage_type="scheduled_erp_image",
        original_filename=build_archive_filename(product.name, "jpg"),
    )
    if not asset:
        raise ErpProductApiError(f"ERP 图片“{product.name}”导入本地素材库失败")
    return asset, storage_service.get_url(asset.storage_key)
