"""幂等初始化中西无界、写怀和剪纸系列的三图海报发布链路。

脚本只处理 ``NEW_BRAND_POSTER_CONFIGS`` 中的三个新增来源：

* 从用户提供的本地 TXT 读取微信公众号 AppID/AppSecret，并以现有 AES-GCM
  凭证服务加密写入 MySQL；
* 复用或补齐三品牌的文章格式库、背景库和三图格式模板；
* 为每个品牌创建或更新一条私域 08:00 和一条公域 13:00 定时任务；
* 为两条任务写入同一个品牌级 ERP 防重范围，保证三天内不重复选品。

脚本不调用全量品牌重建函数，也不查询或修改绣蔓任务。重复运行只更新本次三个
品牌的目标记录，适合部署后重新执行。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 直接执行 scripts 下的文件时，Python 默认只把 scripts 加入 sys.path；补齐
# backend 根目录后，脚本与 Worker 使用完全相同的配置和 ORM 模块。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings
from app.database import MysqlSessionLocal, PgSessionLocal
from app.models.mysql_models import (
    AccountCredential,
    ArticleFormatProfile,
    ScheduledTask,
    WeChatAccount,
)
from app.models.pg_models import KbDocument, KnowledgeBase
from app.services.brand_poster_task_configuration import (
    NEW_BRAND_POSTER_CONFIGS,
    BrandPosterTaskConfig,
    build_three_image_template_payload,
)
from app.services.encryption_service import derive_key, encrypt_secret
from app.services.erp_product_service import parse_erp_product_sources
from app.services.knowledge_base_service import create_knowledge_base, process_document

# 复用已经验证过的品牌格式和背景文档内容，但不调用该文件的全量迁移入口。
from scripts.rebuild_brand_split_knowledge_bases import BRAND_SPLIT_KNOWLEDGE


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrandCredential:
    """从用户 TXT 中解析出的单个公众号凭证，仅在进程内短暂存在。"""

    source_key: str
    app_id: str
    app_secret: str


_SOURCE_KEY_BY_CLIENT_ID_SUFFIX = {
    "-zxwj": "zhongxiwujie",
    "-xh": "xiehuai",
    "-jz": "jianzhi",
}


def load_brand_credentials(credentials_path: str | Path) -> dict[str, BrandCredential]:
    """读取 TXT 开头的 JSON 数组并提取三个新品牌凭证。

    TXT 后半段包含品牌知识资料，不能用整文件 ``json.loads``；JSONDecoder 只解析
    开头数组，既兼容当前文件格式，也避免把文档内容带入账号初始化逻辑。
    """

    path = Path(credentials_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到公众号配置文件：{path}")
    raw_text = path.read_text(encoding="utf-8-sig")
    array_start = raw_text.find("[")
    if array_start < 0:
        raise ValueError("公众号配置文件开头没有找到 JSON 数组")
    try:
        records, _ = json.JSONDecoder().raw_decode(raw_text[array_start:])
    except json.JSONDecodeError as exc:
        raise ValueError("公众号配置文件开头的 JSON 数组无法解析") from exc
    if not isinstance(records, list):
        raise ValueError("公众号配置文件的 JSON 配置必须是数组")

    credentials: dict[str, BrandCredential] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        client_id = str(record.get("erp_client_id") or "").strip()
        source_key = next(
            (
                mapped_key
                for suffix, mapped_key in _SOURCE_KEY_BY_CLIENT_ID_SUFFIX.items()
                if client_id.endswith(suffix)
            ),
            None,
        )
        if source_key is None or source_key not in NEW_BRAND_POSTER_CONFIGS:
            continue
        app_id = str(record.get("wechat_app_id") or "").strip()
        app_secret = str(record.get("wechat_app_secret") or "").strip()
        if not app_id or not app_secret:
            raise ValueError(f"品牌 {source_key} 缺少微信公众号凭证")
        credentials[source_key] = BrandCredential(
            source_key=source_key,
            app_id=app_id,
            app_secret=app_secret,
        )

    missing = sorted(set(NEW_BRAND_POSTER_CONFIGS) - set(credentials))
    if missing:
        raise ValueError(f"公众号配置文件缺少品牌凭证：{', '.join(missing)}")
    return credentials


def _knowledge_items_by_source() -> dict[str, Any]:
    """按来源键索引已审核的品牌格式/背景文档定义。"""

    return {item.erp_source_key: item for item in BRAND_SPLIT_KNOWLEDGE}


def _ensure_knowledge_base(
    db,
    *,
    tenant_id: int,
    name: str,
    kb_type: str,
    description: str,
) -> KnowledgeBase:
    """复用同名知识库并恢复启用状态，不创建重复库。"""

    knowledge_base = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.name == name)
        .first()
    )
    if knowledge_base is not None:
        knowledge_base.is_active = 1
        knowledge_base.kb_type = kb_type
        db.commit()
        return knowledge_base
    return create_knowledge_base(
        db,
        tenant_id=tenant_id,
        name=name,
        kb_type=kb_type,
        description=description,
    )


def _ensure_generated_document(
    db,
    *,
    knowledge_base: KnowledgeBase,
    tenant_id: int,
    filename: str,
    content: str,
) -> None:
    """只在目标知识库没有可用系统文档时导入文档，减少重复嵌入成本。"""

    existing = (
        db.query(KbDocument)
        .filter(
            KbDocument.knowledge_base_id == knowledge_base.id,
            KbDocument.filename == filename,
            KbDocument.status == "ready",
        )
        .first()
    )
    if existing is not None:
        return
    document = process_document(
        db,
        knowledge_base.id,
        tenant_id,
        content.encode("utf-8"),
        filename,
    )
    if document.status != "ready":
        raise RuntimeError(
            f"知识库“{knowledge_base.name}”文档处理失败："
            f"{document.error_message or document.status}"
        )


def _ensure_three_image_profile(mysql_db, tenant_id: int) -> ArticleFormatProfile:
    """创建独立的三图模板，不修改现有四图海报模板或绣蔓绑定。"""

    name = "无缝海报三图通用模板 v1"
    profile = (
        mysql_db.query(ArticleFormatProfile)
        .filter(
            ArticleFormatProfile.tenant_id == tenant_id,
            ArticleFormatProfile.name == name,
            ArticleFormatProfile.render_mode == "poster_gallery",
        )
        .first()
    )
    payload = build_three_image_template_payload()
    if profile is None:
        profile = ArticleFormatProfile(
            tenant_id=tenant_id,
            source_article_id=None,
            name=name,
            version=1,
            render_mode="poster_gallery",
            template_payload=payload,
            title_policy={
                "visual_title_mode": "first_poster",
                "wechat_title_source": "generated",
            },
            is_active=True,
        )
        mysql_db.add(profile)
        mysql_db.flush()
    else:
        profile.template_payload = payload
        profile.is_active = True
    return profile


def _ensure_account(mysql_db, tenant_id: int, config: BrandPosterTaskConfig, credential: BrandCredential) -> WeChatAccount:
    """按 AppID 幂等创建公众号并加密更新 AppSecret。"""

    account = (
        mysql_db.query(WeChatAccount)
        .filter(
            WeChatAccount.tenant_id == tenant_id,
            WeChatAccount.app_id == credential.app_id,
            WeChatAccount.deleted_at.is_(None),
        )
        .first()
    )
    if account is None:
        account = WeChatAccount(
            tenant_id=tenant_id,
            name=config.display_name,
            app_id=credential.app_id,
            auth_mode="app_secret",
            status="active",
        )
        mysql_db.add(account)
        mysql_db.flush()
    else:
        account.name = config.display_name
        account.auth_mode = "app_secret"
        account.status = "active"

    encrypted_secret = encrypt_secret(
        credential.app_secret,
        derive_key(settings.credential_key),
    )
    stored_credential = (
        mysql_db.query(AccountCredential)
        .filter(
            AccountCredential.tenant_id == tenant_id,
            AccountCredential.account_id == account.id,
        )
        .first()
    )
    if stored_credential is None:
        mysql_db.add(AccountCredential(
            tenant_id=tenant_id,
            account_id=account.id,
            encrypted_secret=encrypted_secret,
            key_version="v1",
        ))
    else:
        stored_credential.encrypted_secret = encrypted_secret
        stored_credential.key_version = "v1"
    return account


def _ensure_task(
    mysql_db,
    *,
    tenant_id: int,
    account: WeChatAccount,
    config: BrandPosterTaskConfig,
    knowledge_base_ids: list[int],
    format_profile: ArticleFormatProfile,
    footer_template: str,
    name: str,
    publish_times: tuple[str, ...],
    publish_domain: str,
) -> ScheduledTask:
    """按租户和任务名幂等保存一条品牌海报任务。"""

    task = (
        mysql_db.query(ScheduledTask)
        .filter(ScheduledTask.tenant_id == tenant_id, ScheduledTask.name == name)
        .first()
    )
    if task is None:
        task = ScheduledTask(tenant_id=tenant_id, name=name)
        mysql_db.add(task)

    task.is_active = True
    task.writing_mode = "kb"
    task.topic = None
    # 三个新品牌的标题规则通过公共模板复用。任务只保存编号，文案细则集中在
    # 模板服务维护，避免公私域任务分别存放不可维护的长提示词。
    task.style = config.writing_style_template_id
    task.feed_source_ids = None
    task.feed_source_id = None
    task.feed_article_ids = None
    task.format_profile_id = format_profile.id
    task.format_profile_auto_bind_enabled = False
    task.knowledge_base_ids = knowledge_base_ids
    task.day_of_week = -1
    task.publish_times = list(publish_times)
    task.articles_per_day = 1
    task.html_image_count = 5
    task.public_count = 1 if publish_domain == "public" else 0
    task.private_count = 1 if publish_domain == "private" else 0
    task.approval_mode = "auto"
    task.account_id = None
    task.account_ids = [account.id]
    task.publish_mode = "direct"
    task.publish_domain = publish_domain
    task.image_source = "ERP"
    task.footer_template = footer_template
    task.content_type = "article"
    task.layout_mode = "seamless_poster"
    task.enabled_image_methods = ["ERP"]
    # 新品牌图片规则要求每张海报都有对应品牌水印。水印文字由任务快照锁定，
    # 归档阶段使用中文字体程序绘制，模型提示词只负责产品和背景，避免两层水印
    # 或全局配置变更后历史任务样式漂移。
    task.enable_watermark = True
    task.watermark_config = {
        "enabled": True,
        "type": "text",
        "content": config.watermark_content,
        "font_size": 24,
        "position": config.watermark_position,
        "opacity": 0.9,
        "margin": 40,
        "locked": True,
    }
    task.erp_image_config = {
        "source_key": config.source_key,
        "commodity_category": None,
        "repeat_after_days": 3,
        "image_count": 1,
        "selection_scope": config.selection_scope,
    }
    # 任务 ID 需要在同一事务的结果摘要中可见；flush 不提交事务，也不会让账号、
    # 模板或任务出现半套落库状态，真正提交仍由初始化入口统一完成。
    mysql_db.flush()
    return task


def initialize_new_brand_poster_tasks(
    *,
    credentials_path: str | Path,
    tenant_id: int,
) -> dict[str, Any]:
    """执行三个新品牌的完整初始化并返回脱敏结果。"""

    credentials = load_brand_credentials(credentials_path)
    configured_sources = {
        source.key for source in parse_erp_product_sources(settings.erp_product_sources_json)
    }
    missing_sources = sorted(set(NEW_BRAND_POSTER_CONFIGS) - configured_sources)
    if missing_sources:
        raise RuntimeError(
            "后端 ERP_PRODUCT_SOURCES_JSON 缺少来源：" + ", ".join(missing_sources)
        )

    knowledge_items = _knowledge_items_by_source()
    mysql_db = MysqlSessionLocal()
    pg_db = PgSessionLocal()
    result: dict[str, Any] = {"accounts": {}, "tasks": {}, "knowledge_bases": {}}
    try:
        format_profile = _ensure_three_image_profile(mysql_db, tenant_id)
        for source_key, config in NEW_BRAND_POSTER_CONFIGS.items():
            item = knowledge_items.get(source_key)
            if item is None:
                raise RuntimeError(f"缺少品牌 {source_key} 的格式/背景文档定义")
            account = _ensure_account(mysql_db, tenant_id, config, credentials[source_key])

            format_kb = _ensure_knowledge_base(
                pg_db,
                tenant_id=tenant_id,
                name=config.format_knowledge_base_name,
                kb_type="publication_format",
                description="品牌文章结构、海报文案限制和固定联系方式。",
            )
            visual_kb = _ensure_knowledge_base(
                pg_db,
                tenant_id=tenant_id,
                name=config.visual_knowledge_base_name,
                kb_type="brand_visual",
                description="ERP 产品图生图所需的品牌场景、产品主体和背景规则。",
            )
            _ensure_generated_document(
                pg_db,
                knowledge_base=format_kb,
                tenant_id=tenant_id,
                filename="系统生成：文章格式规则.txt",
                content=item.format_document_text,
            )
            _ensure_generated_document(
                pg_db,
                knowledge_base=visual_kb,
                tenant_id=tenant_id,
                filename="系统生成：背景说明.txt",
                content=item.visual_document_text,
            )

            knowledge_ids = [format_kb.id, visual_kb.id]
            private_task = _ensure_task(
                mysql_db,
                tenant_id=tenant_id,
                account=account,
                config=config,
                knowledge_base_ids=knowledge_ids,
                format_profile=format_profile,
                footer_template=item.footer_template,
                name=config.private_task_name,
                publish_times=config.private_publish_times,
                publish_domain="private",
            )
            public_task = _ensure_task(
                mysql_db,
                tenant_id=tenant_id,
                account=account,
                config=config,
                knowledge_base_ids=knowledge_ids,
                format_profile=format_profile,
                footer_template=item.footer_template,
                name=config.public_task_name,
                publish_times=config.public_publish_times,
                publish_domain="public",
            )
            result["accounts"][source_key] = account.id
            result["knowledge_bases"][source_key] = knowledge_ids
            result["tasks"][source_key] = {
                "private": private_task.id,
                "public": public_task.id,
            }

        pg_db.commit()
        mysql_db.commit()
        return result
    except Exception:
        mysql_db.rollback()
        pg_db.rollback()
        raise
    finally:
        pg_db.close()
        mysql_db.close()


def main() -> None:
    """解析命令行参数并执行初始化，日志只输出 ID 和名称。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials-path",
        required=True,
        help="包含公众号/ERP 凭证的本地 TXT 路径",
    )
    parser.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args()
    result = initialize_new_brand_poster_tasks(
        credentials_path=args.credentials_path,
        tenant_id=args.tenant_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
