"""知识库文档列表的租户边界回归测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """该单元测试不访问业务数据库。"""
    yield


def test_list_documents_checks_tenant_on_knowledge_base_not_document():
    """KbDocument 没有 tenant_id，文档列表必须通过 KnowledgeBase 校验租户。"""
    from app.services.knowledge_base_service import list_documents

    class FakeQuery:
        def __init__(self, result=None):
            self.result = result

        def filter(self, *conditions):
            return self

        def order_by(self, *columns):
            return self

        def all(self):
            return ["document"]

        def first(self):
            return self.result

    class FakeDb:
        def query(self, model):
            from app.models.pg_models import KnowledgeBase

            if model is KnowledgeBase:
                return FakeQuery(result=object())
            return FakeQuery()

    assert list_documents(FakeDb(), kb_id=1, tenant_id=107) == ["document"]


def test_list_document_chunks_returns_content_in_original_order():
    """文档预览必须按切片索引顺序返回正文，而不是按数据库插入顺序。"""
    from app.services.knowledge_base_service import list_document_chunks

    class Chunk:
        def __init__(self, index, content):
            self.chunk_index = index
            self.content = content

    class FakeQuery:
        def filter(self, *conditions):
            return self

        def order_by(self, *columns):
            return self

        def all(self):
            return [Chunk(0, "第一段"), Chunk(1, "第二段")]

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

    assert list_document_chunks(FakeDb(), document_id=1) == [
        {"chunk_index": 0, "content": "第一段"},
        {"chunk_index": 1, "content": "第二段"},
    ]
