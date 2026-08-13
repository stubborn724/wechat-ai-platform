"""定时海报任务的同品牌知识库接入测试。"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证数据库查询适配层，不依赖真实业务数据。"""

    yield


class _FakeQuery:
    """只实现路由适配层需要的查询链，避免测试连接 PostgreSQL。"""

    def __init__(self, records):
        self.records = records

    def filter(self, *conditions):
        del conditions
        return self

    def all(self):
        return self.records


class _FakeDb:
    """返回固定知识库记录的最小数据库替身。"""

    def __init__(self, records):
        self.records = records

    def query(self, model):
        del model
        return _FakeQuery(self.records)


def test_poster_task_resolves_xiuman_format_and_visual_kbs_from_erp_source():
    """定时任务只绑定背景库时，执行适配层仍按 ERP 来源补齐格式库。"""
    from app.services.brand_knowledge_routing import (
        resolve_brand_knowledge_base_ids_for_task,
    )

    records = [
        SimpleNamespace(id=9, name="绣蔓家具文章格式规则", kb_type="publication_format", is_active=1),
        SimpleNamespace(id=10, name="绣蔓家具背景说明", kb_type="brand_visual", is_active=1),
    ]

    result = resolve_brand_knowledge_base_ids_for_task(
        db=_FakeDb(records),
        tenant_id=107,
        source_key="xiuman",
        configured_ids=[10],
    )

    assert result == [9, 10]
