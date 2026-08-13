"""初始化她格原创公众号知识库和公域、私域定时任务。

该脚本只管理“她格原创-公域”和“她格原创-私域”两个明确命名的任务。她格内容
面向中小企业的 AI 转型与入企服务，因此不复用家具产品、ERP 选品、投喂源仿写、
格式模板或无缝海报链路。重复执行时复用同名知识库和任务，适合后续更新资料或
补充企业微信二维码后再次运行，且不会触碰绣蔓及其他已上线任务。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 直接执行 scripts 下的文件时，Python 默认只把 scripts 加入 sys.path；补齐
# backend 根目录后，脚本与 Web 服务、Worker 使用相同的 ORM 和配置模块。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import MysqlSessionLocal, PgSessionLocal
from app.models.mysql_models import ScheduledTask, WeChatAccount
from app.models.pg_models import KbDocument, KnowledgeBase
from app.services.footer_template_service import build_consultation_card_template
from app.services.writing_style_template_service import (
    SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
)
from app.services.knowledge_base_service import (
    create_knowledge_base,
    parse_document,
    process_document,
)


SHEGE_BRAND_NAME = "她格"
SHEGE_ACCOUNT_ID = 103
SHEGE_ACCOUNT_NAME = "主号"
SHEGE_PHONE = "18613093631"
SHEGE_FOOTER_HEADLINE = "企业 AI 转型咨询"
SHEGE_PUBLIC_TASK_NAME = "她格原创-公域"
SHEGE_PRIVATE_TASK_NAME = "她格原创-私域"
DEFAULT_BRAND_DOCUMENT_PATH = Path(
    r"C:\Users\25479\Documents\WXWork\1688858276864773\Cache\File\2026-08\她格知识库品牌定位.docx"
)
DEFAULT_SERVICE_DOCUMENT_PATH = Path(
    r"C:\Users\25479\Documents\WXWork\1688858276864773\Cache\File\2026-08\她格入企服务的定位.docx"
)


@dataclass(frozen=True)
class ShegeKnowledgeSource:
    """一份她格来源文件及其知识库归属。

    以来源文件路径、知识库名称和展示文件名建立稳定映射，避免用目录扫描把同目录
    其他业务资料意外写入她格检索上下文。
    """

    knowledge_base_name: str
    knowledge_base_description: str
    source_path: Path
    filename: str


def build_shege_task_specs(
    *,
    knowledge_base_ids: list[int],
    account_id: int,
) -> dict[str, dict[str, Any]]:
    """构造她格任务的隔离配置，不包含 ERP、海报或仿写来源字段。

    公域和私域仅在时间和发布域上不同，其余内容策略必须完全一致。集中构造配置
    能确保以后补充二维码或调整文风时只改一处，也让初始化脚本可通过纯函数测试。
    """

    normalized_knowledge_base_ids = [int(item) for item in knowledge_base_ids]
    if not normalized_knowledge_base_ids:
        raise ValueError("她格原创任务至少需要绑定一个知识库")
    if int(account_id) <= 0:
        raise ValueError("主号账号 ID 必须为正整数")

    footer_template = build_consultation_card_template(
        brand=SHEGE_BRAND_NAME,
        headline=SHEGE_FOOTER_HEADLINE,
        phone=SHEGE_PHONE,
        # 企业微信二维码尚未提供时保留空数组。页脚渲染器会输出电话卡片，但不会
        # 渲染空白二维码区；后续只需在此参数补充 URL 即可。
        qrcodes=[],
    )
    base_specification: dict[str, Any] = {
        "is_active": True,
        "writing_mode": "kb",
        "topic": None,
        # 任务仅保存公共模板编号。具体标题、正文和图片规则由模板服务集中维护，
        # 后续新建任务也可复用，避免将不可见的长提示词复制到每条任务记录。
        "style": SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
        "feed_source_ids": None,
        "feed_source_id": None,
        "feed_article_ids": None,
        "format_profile_id": None,
        "format_profile_auto_bind_enabled": False,
        "template_rotation_config": None,
        "template_rotation_version": 0,
        "knowledge_base_ids": normalized_knowledge_base_ids,
        "day_of_week": -1,
        "articles_per_day": 1,
        # 普通原创文章控制为三张主题配图，保证可读性，同时避免海报任务的多图成本。
        "html_image_count": 3,
        "approval_mode": "auto",
        "account_id": None,
        "account_ids": [int(account_id)],
        "publish_mode": "direct",
        "image_source": "dashscope",
        "footer_template": footer_template,
        "content_type": "article",
        "layout_mode": "standard",
        "enabled_image_methods": ["DASHSCOPE"],
        # 显式关闭任务级水印，避免继承家具任务的产品/电话水印。
        "enable_watermark": False,
        "watermark_config": {"enabled": False, "locked": True},
        "erp_image_config": None,
        "article_slots": None,
    }
    return {
        SHEGE_PUBLIC_TASK_NAME: {
            **base_specification,
            "publish_times": ["13:00"],
            "public_count": 1,
            "private_count": 0,
            "publish_domain": "public",
        },
        SHEGE_PRIVATE_TASK_NAME: {
            **base_specification,
            "publish_times": ["08:00", "20:00"],
            "public_count": 0,
            "private_count": 1,
            "publish_domain": "private",
        },
    }


def _default_knowledge_sources(
    *,
    brand_document_path: Path,
    service_document_path: Path,
) -> tuple[ShegeKnowledgeSource, ShegeKnowledgeSource]:
    """返回两份用户指定资料的固定知识库映射。"""

    return (
        ShegeKnowledgeSource(
            knowledge_base_name="她格品牌定位",
            knowledge_base_description="她格品牌边界、服务价值、能力体系与交付原则。",
            source_path=brand_document_path,
            filename="她格知识库品牌定位.docx",
        ),
        ShegeKnowledgeSource(
            knowledge_base_name="她格入企服务定位",
            knowledge_base_description="她格面向中小企业的 AI 入企服务定位与实施路径。",
            source_path=service_document_path,
            filename="她格入企服务的定位.docx",
        ),
    )


def _ensure_knowledge_base(pg_db, *, tenant_id: int, source: ShegeKnowledgeSource) -> KnowledgeBase:
    """按租户和名称复用她格知识库，并恢复此前被停用的知识库。"""

    knowledge_base = (
        pg_db.query(KnowledgeBase)
        .filter(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.name == source.knowledge_base_name,
        )
        .first()
    )
    if knowledge_base is None:
        return create_knowledge_base(
            pg_db,
            tenant_id=tenant_id,
            name=source.knowledge_base_name,
            kb_type="article",
            description=source.knowledge_base_description,
        )

    knowledge_base.is_active = 1
    knowledge_base.kb_type = "article"
    knowledge_base.description = source.knowledge_base_description
    pg_db.flush()
    return knowledge_base


def _ensure_source_document(
    pg_db,
    *,
    tenant_id: int,
    knowledge_base: KnowledgeBase,
    source: ShegeKnowledgeSource,
) -> int:
    """把来源 DOCX 幂等导入知识库，内容变化时替换旧文档及其向量切片。

    先按解析后的文本计算哈希，避免同一文件仅元数据变化时重复调用嵌入模型。若业务
    文档确实更新，旧文档标记为 deleted 后再导入新版本，检索时不会混用过期资料。
    """

    if not source.source_path.is_file():
        raise FileNotFoundError(f"找不到她格知识库来源文件：{source.source_path}")
    file_bytes = source.source_path.read_bytes()
    parsed_content = parse_document(file_bytes, source.filename)
    if not parsed_content or not parsed_content.strip():
        raise ValueError(f"她格知识库来源文件无法解析有效文本：{source.source_path}")
    content_hash = hashlib.sha256(parsed_content.encode("utf-8")).hexdigest()
    current_document = (
        pg_db.query(KbDocument)
        .filter(
            KbDocument.knowledge_base_id == knowledge_base.id,
            KbDocument.filename == source.filename,
            KbDocument.status == "ready",
        )
        .order_by(KbDocument.id.desc())
        .first()
    )
    if current_document is not None and current_document.content_hash == content_hash:
        return int(current_document.id)

    if current_document is not None:
        # ``process_document`` 自带事务提交；提前软删除旧版本可防止来源资料更新时
        # 新旧切片同时参与检索。该状态同时会让知识库文档列表隐藏历史版本。
        current_document.status = "deleted"
        pg_db.commit()

    document = process_document(
        pg_db,
        knowledge_base.id,
        tenant_id,
        file_bytes,
        source.filename,
    )
    if document.status != "ready":
        raise RuntimeError(
            f"知识库“{knowledge_base.name}”导入失败："
            f"{document.error_message or document.status}"
        )
    return int(document.id)


def _resolve_main_account(mysql_db) -> WeChatAccount:
    """解析已验证的主号账号，避免把她格任务绑到其他公众号。"""

    account = (
        mysql_db.query(WeChatAccount)
        .filter(
            WeChatAccount.id == SHEGE_ACCOUNT_ID,
            WeChatAccount.deleted_at.is_(None),
        )
        .first()
    )
    if account is None:
        raise RuntimeError(f"未找到主号账号 ID={SHEGE_ACCOUNT_ID}")
    if account.name != SHEGE_ACCOUNT_NAME:
        raise RuntimeError(
            f"账号 ID={SHEGE_ACCOUNT_ID} 不是预期主号：实际名称为“{account.name}”"
        )
    return account


def _ensure_shege_task(
    mysql_db,
    *,
    tenant_id: int,
    name: str,
    specification: dict[str, Any],
) -> ScheduledTask:
    """按精确任务名创建或更新她格任务，写入范围严格限制在她格命名空间。"""

    if name not in {SHEGE_PUBLIC_TASK_NAME, SHEGE_PRIVATE_TASK_NAME}:
        raise ValueError(f"拒绝更新非她格任务：{name}")
    task = (
        mysql_db.query(ScheduledTask)
        .filter(ScheduledTask.tenant_id == tenant_id, ScheduledTask.name == name)
        .first()
    )
    if task is None:
        task = ScheduledTask(tenant_id=tenant_id, name=name)
        mysql_db.add(task)

    for field_name, value in specification.items():
        setattr(task, field_name, value)
    mysql_db.flush()
    return task


def initialize_shege_original_tasks(
    *,
    brand_document_path: Path = DEFAULT_BRAND_DOCUMENT_PATH,
    service_document_path: Path = DEFAULT_SERVICE_DOCUMENT_PATH,
) -> dict[str, Any]:
    """导入她格资料并创建每日公域、私域任务，返回不含敏感信息的配置摘要。"""

    mysql_db = MysqlSessionLocal()
    pg_db = PgSessionLocal()
    try:
        account = _resolve_main_account(mysql_db)
        sources = _default_knowledge_sources(
            brand_document_path=Path(brand_document_path),
            service_document_path=Path(service_document_path),
        )
        knowledge_base_ids: list[int] = []
        document_ids: list[int] = []
        for source in sources:
            knowledge_base = _ensure_knowledge_base(
                pg_db,
                tenant_id=account.tenant_id,
                source=source,
            )
            document_id = _ensure_source_document(
                pg_db,
                tenant_id=account.tenant_id,
                knowledge_base=knowledge_base,
                source=source,
            )
            knowledge_base_ids.append(int(knowledge_base.id))
            document_ids.append(document_id)
        pg_db.commit()

        task_specs = build_shege_task_specs(
            knowledge_base_ids=knowledge_base_ids,
            account_id=account.id,
        )
        tasks = {
            name: _ensure_shege_task(
                mysql_db,
                tenant_id=account.tenant_id,
                name=name,
                specification=specification,
            )
            for name, specification in task_specs.items()
        }
        mysql_db.commit()
        return {
            "account_id": int(account.id),
            "knowledge_base_ids": knowledge_base_ids,
            "document_ids": document_ids,
            "task_ids": {name: int(task.id) for name, task in tasks.items()},
        }
    except Exception:
        mysql_db.rollback()
        pg_db.rollback()
        raise
    finally:
        pg_db.close()
        mysql_db.close()


def main() -> None:
    """解析可选来源路径后执行初始化，输出便于部署核验的脱敏 ID 摘要。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-document-path",
        type=Path,
        default=DEFAULT_BRAND_DOCUMENT_PATH,
        help="她格知识库品牌定位 DOCX 的本地路径",
    )
    parser.add_argument(
        "--service-document-path",
        type=Path,
        default=DEFAULT_SERVICE_DOCUMENT_PATH,
        help="她格入企服务定位 DOCX 的本地路径",
    )
    args = parser.parse_args()
    result = initialize_shege_original_tasks(
        brand_document_path=args.brand_document_path,
        service_document_path=args.service_document_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
