"""从投稿配置文件导入脱敏后的品牌知识库，并绑定 ERP 定时任务。

该脚本刻意不导入公众号密钥、ERP 密钥、接口路径和服务器地址。配置文件可能同时
包含业务规则与运维凭证，只有前者可进入向量库并参与文章和图片提示词生成。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.database import MysqlSessionLocal, PgSessionLocal
from app.models.mysql_models import ScheduledTask
from app.models.pg_models import KnowledgeBase
from app.services.knowledge_base_service import create_knowledge_base, process_document


SOURCE_FILE = Path(r"C:\Users\25479\Documents\WXWork\1688858276864773\Cache\File\2026-07\公众号投稿提示词和相关配置.txt")

# ERP 来源和任务一一对应，确保检索到的品牌规则不会串到其他产品图任务。
BRAND_CONFIG = {
    "xiuman": ("绣蔓家具品牌规则", "绣蔓家具"),
    "zhongxiwujie": ("中西无界品牌规则", "中西无界"),
    "xiehuai": ("写怀品牌规则", "写怀"),
    "jianzhi": ("剪纸系列品牌规则", "剪纸系列"),
}


def _section(text: str, start: str, end: str | None = None) -> str:
    """截取品牌资料区间，找不到边界时返回空串而不误导入整份配置。"""
    start_index = text.find(start)
    if start_index < 0:
        return ""
    body = text[start_index:]
    if end:
        end_index = body.find(end)
        if end_index >= 0:
            body = body[:end_index]
    return body


def _first_requirement(text: str) -> str:
    """提取首个绣蔓投稿要求，避免把其后的认证配置写入知识库。"""
    match = re.search(r'"user_requirement"\s*:\s*"(.*?)"\s*,\s*"wechat_app_id"', text, re.S)
    if not match:
        return ""
    return match.group(1).replace("\\n", "\n").replace('\\"', '"')


def sanitize(text: str) -> str:
    """剔除凭证与网络配置，仅保留品牌、文案、视觉、联系方式等公开业务资料。"""
    forbidden = re.compile(
        r"(secret|app_id|client_id|api_path|token|password|authorization|\b\d{1,3}(?:\.\d{1,3}){3}\b)",
        re.I,
    )
    kept_lines = []
    for line in text.splitlines():
        if forbidden.search(line):
            continue
        kept_lines.append(line.rstrip())
    sanitized = "\n".join(kept_lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized


def build_brand_documents(raw: str) -> dict[str, str]:
    """按品牌提取可检索资料；每个文档独立，保证向量检索边界清晰。"""
    return {
        "xiuman": sanitize(_first_requirement(raw)),
        "jianzhi": sanitize(_section(raw, "# 剪纸3.0系列相关知识", "写怀：")),
        "xiehuai": sanitize(_section(raw, "写怀：", "中西无界：")),
        "zhongxiwujie": sanitize(_section(raw, "中西无界：")),
    }


def get_or_create_knowledge_base(pg_db, tenant_id: int, name: str) -> KnowledgeBase:
    """按租户和名称复用知识库，脚本可重复执行且不会产生同品牌副本。"""
    existing = (
        pg_db.query(KnowledgeBase)
        .filter(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.name == name)
        .first()
    )
    if existing:
        return existing
    return create_knowledge_base(
        pg_db,
        tenant_id=tenant_id,
        name=name,
        kb_type="brand_visual",
        description="用于 ERP 产品图生图、海报文案和公众号投稿版式的脱敏品牌规则。",
    )


def run_import() -> None:
    """导入四份品牌资料并将知识库 ID 写回对应的 ERP 定时任务。"""
    raw = SOURCE_FILE.read_text(encoding="utf-8")
    documents = build_brand_documents(raw)
    mysql_db = MysqlSessionLocal()
    pg_db = PgSessionLocal()
    try:
        tasks = (
            mysql_db.query(ScheduledTask)
            .filter(ScheduledTask.erp_image_config.isnot(None))
            .all()
        )
        for task in tasks:
            source_key = str((task.erp_image_config or {}).get("source_key") or "")
            brand = BRAND_CONFIG.get(source_key)
            if not brand:
                continue
            kb_name, _brand_label = brand
            content = documents.get(source_key, "")
            if not content:
                raise ValueError(f"未提取到 {kb_name} 的脱敏品牌资料")
            kb = get_or_create_knowledge_base(pg_db, task.tenant_id, kb_name)
            document = process_document(
                pg_db,
                kb.id,
                task.tenant_id,
                content.encode("utf-8"),
                f"{_brand_label}品牌视觉与投稿规则.txt",
            )
            if document.status not in {"ready", "duplicate"}:
                raise RuntimeError(f"{kb_name} 入库失败：{document.error_message}")
            task.knowledge_base_ids = [kb.id]
            print(f"任务 #{task.id} 已绑定知识库 #{kb.id}：{kb_name}（{document.status}）")
        mysql_db.commit()
    finally:
        pg_db.close()
        mysql_db.close()


if __name__ == "__main__":
    run_import()
