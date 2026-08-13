"""ERP 来源与品牌知识库路由的契约测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """路由纯函数只消费内存记录，不应触发业务数据库清理。"""

    yield


def test_xiuman_source_adds_matching_format_kb_when_task_only_has_visual_kb():
    """绣蔓 ERP 任务只绑定背景库时，海报路由应补齐同品牌格式库。"""
    from app.services.brand_knowledge_routing import resolve_brand_knowledge_base_ids

    records = [
        SimpleNamespace(id=9, name="绣蔓家具文章格式规则", kb_type="publication_format", is_active=1),
        SimpleNamespace(id=10, name="绣蔓家具背景说明", kb_type="brand_visual", is_active=1),
        SimpleNamespace(id=11, name="中西无界背景说明", kb_type="brand_visual", is_active=1),
    ]

    result = resolve_brand_knowledge_base_ids(
        source_key="xiuman",
        configured_ids=[10],
        knowledge_bases=records,
    )

    assert result == [9, 10]


def test_unknown_source_keeps_explicit_knowledge_base_ids_only():
    """未登记的 ERP 来源不能猜测或串用其他品牌知识库。"""
    from app.services.brand_knowledge_routing import resolve_brand_knowledge_base_ids

    records = [
        SimpleNamespace(id=9, name="绣蔓家具文章格式规则", kb_type="publication_format", is_active=1),
        SimpleNamespace(id=10, name="绣蔓家具背景说明", kb_type="brand_visual", is_active=1),
    ]

    result = resolve_brand_knowledge_base_ids(
        source_key="unknown-brand",
        configured_ids=[10],
        knowledge_bases=records,
    )

    assert result == [10]
