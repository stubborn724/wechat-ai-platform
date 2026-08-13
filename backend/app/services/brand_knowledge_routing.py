"""ERP 来源到品牌知识库的确定性路由。

定时海报必须同时使用同品牌的文章格式规则和产品背景规则。过去任务只绑定
背景库时，运行时无法识别纯海报格式；本模块把 ERP 来源键作为唯一业务边界，
在不修改任务数据的前提下补齐同品牌格式库，避免不同品牌的视觉规则串用。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.pg_models import KnowledgeBase


@dataclass(frozen=True)
class BrandKnowledgeRoute:
    """一个 ERP 来源对应的文章格式库和背景库名称。"""

    source_key: str
    format_knowledge_base_name: str
    visual_knowledge_base_name: str


# 名称与 ``rebuild_brand_split_knowledge_bases.py`` 生成的系统知识库保持一致。
# 运行时只使用来源键和名称，不把品牌联系方式、ERP 凭证等内容带入路由层。
BRAND_KNOWLEDGE_ROUTES: tuple[BrandKnowledgeRoute, ...] = (
    BrandKnowledgeRoute(
        source_key="xiuman",
        format_knowledge_base_name="绣蔓家具文章格式规则",
        visual_knowledge_base_name="绣蔓家具背景说明",
    ),
    BrandKnowledgeRoute(
        source_key="zhongxiwujie",
        format_knowledge_base_name="中西无界文章格式规则",
        visual_knowledge_base_name="中西无界背景说明",
    ),
    BrandKnowledgeRoute(
        source_key="xiehuai",
        format_knowledge_base_name="写怀文章格式规则",
        visual_knowledge_base_name="写怀背景说明",
    ),
    BrandKnowledgeRoute(
        source_key="jianzhi",
        format_knowledge_base_name="剪纸系列文章格式规则",
        visual_knowledge_base_name="剪纸系列背景说明",
    ),
)

_ROUTES_BY_SOURCE_KEY = {item.source_key: item for item in BRAND_KNOWLEDGE_ROUTES}


def get_brand_knowledge_route(source_key: str | None) -> BrandKnowledgeRoute | None:
    """按 ERP 来源键返回品牌知识库路由，未知来源不做猜测。"""

    normalized_source_key = str(source_key or "").strip().lower()
    return _ROUTES_BY_SOURCE_KEY.get(normalized_source_key)


def resolve_brand_knowledge_base_ids(
    *,
    source_key: str | None,
    configured_ids: Iterable[int] | None,
    knowledge_bases: Iterable[Any],
) -> list[int]:
    """补齐同品牌知识库 ID，并保留任务显式绑定的有效 ID。

    ``knowledge_bases`` 必须已经按当前租户和启用状态查询完成。本函数只做内存
    决策，方便测试并避免把租户过滤、数据库连接和业务判断混在一起。未知 ERP
    来源不会从其他品牌中选择知识库；已知来源缺少某个知识库时也不会伪造 ID，
    后续发布格式校验会给出明确错误。
    """

    resolved_ids = {
        int(item)
        for item in (configured_ids or [])
        if str(item).strip()
    }
    route = get_brand_knowledge_route(source_key)
    if route is None:
        return sorted(resolved_ids)

    expected_names = {
        route.format_knowledge_base_name,
        route.visual_knowledge_base_name,
    }
    for knowledge_base in knowledge_bases:
        if not bool(getattr(knowledge_base, "is_active", 1)):
            continue
        if str(getattr(knowledge_base, "name", "") or "").strip() not in expected_names:
            continue
        knowledge_base_id = getattr(knowledge_base, "id", None)
        if knowledge_base_id is not None:
            resolved_ids.add(int(knowledge_base_id))
    return sorted(resolved_ids)


def resolve_brand_knowledge_base_ids_for_task(
    *,
    db: Session,
    tenant_id: int,
    source_key: str | None,
    configured_ids: Iterable[int] | None,
) -> list[int]:
    """读取当前租户启用知识库，并为一个海报任务补齐同品牌 ID。

    数据库过滤放在适配函数中，纯函数继续负责路由决策。这样 Celery 执行器只
    需要调用一个稳定入口，测试也可以用最小查询替身验证租户边界和路由结果。
    """

    knowledge_bases = (
        db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.is_active == 1,
        )
        .all()
    )
    return resolve_brand_knowledge_base_ids(
        source_key=source_key,
        configured_ids=configured_ids,
        knowledge_bases=knowledge_bases,
    )
