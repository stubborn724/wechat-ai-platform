"""素材轮换规则 — 产品/素材在多篇文章间循环使用

支持轮换策略:
  - round_robin: 按顺序循环选择
  - random: 随机选择
"""

import logging
import random
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import Asset

logger = logging.getLogger(__name__)


def select_rotation_assets(
    db: Session,
    tenant_id: int,
    count: int = 1,
    strategy: str = "round_robin",
    tag_filter: Optional[str] = None,
    exclude_ids: Optional[List[int]] = None,
) -> List[Asset]:
    """从素材库中按轮换策略选取素材

    Args:
        db: 数据库 Session
        tenant_id: 租户 ID
        count: 需要选取的素材数量
        strategy: 轮换策略 — round_robin / random
        tag_filter: 按标签过滤（可选）
        exclude_ids: 排除的素材 ID 列表（已用过的）

    Returns:
        选中的 Asset 列表
    """
    query = db.query(Asset).filter(
        Asset.tenant_id == tenant_id,
        Asset.asset_type == "image",
    )

    if tag_filter:
        from sqlalchemy import String, cast
        query = query.filter(cast(Asset.tags, String).like(f'%{tag_filter}%'))

    if exclude_ids:
        query = query.filter(~Asset.id.in_(exclude_ids))

    if strategy == "random":
        all_assets = query.all()
        if not all_assets:
            return []
        return random.sample(all_assets, min(count, len(all_assets)))
    else:
        # round_robin: order by usage_count asc, last used asc
        assets = (
            query.order_by(Asset.usage_count.asc(), Asset.updated_at.asc())
            .limit(count)
            .all()
        )
        return assets


def record_asset_usage(db: Session, asset_ids: List[int]) -> None:
    """记录素材使用次数"""
    for aid in asset_ids:
        asset = db.query(Asset).filter(Asset.id == aid).first()
        if asset:
            asset.usage_count = (asset.usage_count or 0) + 1
    db.commit()
