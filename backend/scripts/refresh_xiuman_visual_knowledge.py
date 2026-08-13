"""只刷新绣蔓 ERP 图生图的背景知识库。

该脚本用于视觉规则修订后的定向同步。它只替换租户内“绣蔓家具背景说明”中的
系统生成文档，不修改定时任务的时间、发布账号、产品轮换记录、文章格式库或其他
品牌知识库，避免日常视觉优化扩大影响范围。
"""

from __future__ import annotations

import sys
from pathlib import Path


# 从 scripts 目录直接运行时，需显式把 backend 根目录加入模块搜索路径。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import PgSessionLocal
from app.models.pg_models import KnowledgeBase
from scripts.rebuild_brand_split_knowledge_bases import (
    BRAND_SPLIT_KNOWLEDGE,
    _replace_generated_document,
)


XIUMAN_SOURCE_KEY = "xiuman"
SYSTEM_DOCUMENT_FILENAME = "系统生成：背景说明.txt"


def refresh_xiuman_visual_knowledge(*, tenant_id: int = 107) -> dict[str, object]:
    """替换绣蔓背景规则的系统文档，并返回可审计的同步结果。"""

    profile = next(
        item
        for item in BRAND_SPLIT_KNOWLEDGE
        if item.erp_source_key == XIUMAN_SOURCE_KEY
    )
    pg_db = PgSessionLocal()
    try:
        knowledge_base = (
            pg_db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.name == profile.visual_knowledge_base_name,
            )
            .first()
        )
        if knowledge_base is None:
            raise RuntimeError(
                f"未找到绣蔓背景知识库：tenant_id={tenant_id}, "
                f"name={profile.visual_knowledge_base_name}"
            )

        _replace_generated_document(
            pg_db,
            knowledge_base=knowledge_base,
            tenant_id=tenant_id,
            filename=SYSTEM_DOCUMENT_FILENAME,
            content=profile.visual_document_text,
        )
        return {
            "tenant_id": tenant_id,
            "knowledge_base_id": knowledge_base.id,
            "knowledge_base_name": knowledge_base.name,
            "document_filename": SYSTEM_DOCUMENT_FILENAME,
            "content_length": len(profile.visual_document_text),
        }
    finally:
        pg_db.close()


if __name__ == "__main__":
    print(refresh_xiuman_visual_knowledge())
