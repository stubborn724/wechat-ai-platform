"""评论线索服务 — 线索 CRUD、同步创建、公开回复"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models.mysql_models import Article, CommentLead, SyncJob, WeChatComment, WeChatAccount, User

logger = logging.getLogger(__name__)

# 公开回复模板（V1 简单模板，V2 接入 AI）
REPLY_TEMPLATES = {
    "purchase": "您好，感谢您的关注！关于「{keyword}」的详细信息，您可以在公众号对话框回复「{keyword}」，我们会将详细资料发送给您。",
    "price": "您好，感谢您的关注！关于价格问题，请在公众号对话框回复「{keyword}」，我们会将最新报价和详细资料发送给您。",
    "cooperation": "您好，感谢您的合作意向！请在公众号对话框回复「合作」，我们的商务同事会与您联系。",
    "after_sale": "您好，感谢您的反馈。请通过公众号对话框描述您的问题，我们的客服同事会尽快为您处理。",
    "interaction": "您好，感谢您的留言！",
    "default": "您好，感谢您的关注！如需了解更多信息，请在公众号对话框回复「{keyword}」。",
}

# 队列定义 → SQL 过滤条件
QUEUE_FILTERS = {
    "all": {},
    "mine": {"assigned_to": "self", "status_not_in": ["closed", "converted", "failed"]},
    "pending_reply": {"reply_content": None, "status_not_in": ["closed", "converted"]},
    "eligible": {"status": "eligible"},
    "awaiting_user": {"status": "awaiting_user"},
    "sent": {"status": "contact_sent"},
    "converted": {"status": "converted"},
    "abnormal": {"status": "failed"},
}

# 队列 → 显示名称
QUEUE_LABELS = {
    "all": "全部",
    "mine": "我的",
    "pending_reply": "待回复",
    "eligible": "可私信",
    "awaiting_user": "待用户联系",
    "sent": "资料已发送",
    "converted": "已转化",
    "abnormal": "异常",
}


def _build_queue_query(db: Session, tenant_id: int, queue: str, current_user_id: Optional[int] = None):
    """构建队列查询"""
    q = db.query(CommentLead).filter(CommentLead.tenant_id == tenant_id)
    filters = QUEUE_FILTERS.get(queue, {})

    if not filters:
        return q

    if "status" in filters:
        q = q.filter(CommentLead.status == filters["status"])
    if "status_not_in" in filters:
        q = q.filter(~CommentLead.status.in_(filters["status_not_in"]))
    if "reply_content" in filters and filters["reply_content"] is None:
        q = q.filter(CommentLead.reply_content.is_(None))

    # "mine" 队列特殊处理
    if "assigned_to" in filters and filters["assigned_to"] == "self":
        if current_user_id:
            q = q.filter(CommentLead.assigned_to == current_user_id)
        else:
            q = q.filter(CommentLead.assigned_to.is_(None))

    return q


def list_leads(
    db: Session,
    tenant_id: int,
    queue: str = "all",
    page: int = 1,
    page_size: int = 20,
    account_id: Optional[int] = None,
    intent_type: Optional[str] = None,
    operator_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user_id: Optional[int] = None,
) -> tuple[list[dict], int]:
    """线索列表（摘要）"""
    q = _build_queue_query(db, tenant_id, queue, current_user_id)

    if account_id:
        q = q.filter(CommentLead.account_id == account_id)
    if intent_type:
        q = q.filter(CommentLead.intent_type == intent_type)
    if operator_id:
        q = q.filter(CommentLead.assigned_to == operator_id)
    if keyword:
        q = q.join(WeChatComment, CommentLead.comment_id == WeChatComment.id).filter(
            WeChatComment.content.ilike(f"%{keyword}%")
        )

    total = q.count()
    rows = (
        q.order_by(CommentLead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for lead in rows:
        comment = db.query(WeChatComment).filter(WeChatComment.id == lead.comment_id).first()
        assigned_user = db.query(User).filter(User.id == lead.assigned_to).first() if lead.assigned_to else None

        # 查文章标题（Article 或 WeChatSyncedArticle）
        article_title = ""
        _aid = comment.article_id if comment else None
        if _aid:
            art = db.query(Article).filter(Article.id == _aid).first()
            if art:
                article_title = art.main_title or art.topic or ""
            if not article_title:
                from app.models.mysql_models import WeChatSyncedArticle
                sart = db.query(WeChatSyncedArticle).filter(
                    WeChatSyncedArticle.id == _aid
                ).first()
                if sart:
                    article_title = sart.title or ""

        # 查公众号名称
        account_name = ""
        if lead.account_id:
            acct = db.query(WeChatAccount).filter(WeChatAccount.id == lead.account_id).first()
            if acct:
                account_name = acct.name or ""

        items.append({
            "id": lead.id,
            "account_id": lead.account_id,
            "account_name": account_name,
            "openid": lead.openid,
            "nickname": comment.nickname if comment else "",
            "comment_content": comment.content if comment else "",
            "comment_time": comment.create_time.isoformat() if comment and comment.create_time else None,
            "article_title": article_title,
            "article_id": comment.article_id if comment else None,
            "intent_type": lead.intent_type,
            "intent_score": lead.intent_score,
            "reply_type": lead.reply_type,
            "reply_content": bool(lead.reply_content),
            "lead_status": lead.status,
            "eligibility": lead.eligibility_cache,
            "assigned_to_name": assigned_user.display_name if assigned_user else None,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
        })

    return items, total


def get_lead(db: Session, tenant_id: int, lead_id: int) -> Optional[dict]:
    """线索详情（含评论原文）"""
    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == tenant_id,
    ).first()
    if not lead:
        return None

    comment = db.query(WeChatComment).filter(WeChatComment.id == lead.comment_id).first()
    assigned_user = db.query(User).filter(User.id == lead.assigned_to).first() if lead.assigned_to else None
    account = db.query(WeChatAccount).filter(WeChatAccount.id == lead.account_id).first()

    return {
        "id": lead.id,
        "tenant_id": lead.tenant_id,
        "account_id": lead.account_id,
        "account_name": account.name if account else "",
        "comment_id": lead.comment_id,
        "openid": lead.openid,
        "nickname": comment.nickname if comment else "",
        "comment_content": comment.content if comment else "",
        "comment_time": comment.create_time.isoformat() if comment and comment.create_time else None,
        "article_id": comment.article_id if comment else None,
        "msg_id": comment.msg_id if comment else "",
        "intent_type": lead.intent_type,
        "intent_score": lead.intent_score,
        "intent_analyzed_at": lead.intent_analyzed_at.isoformat() if lead.intent_analyzed_at else None,
        "reply_type": lead.reply_type,
        "reply_content": lead.reply_content,
        "replied_at": lead.replied_at.isoformat() if lead.replied_at else None,
        "eligibility": lead.eligibility_cache,
        "status": lead.status,
        "assigned_to": lead.assigned_to,
        "assigned_to_name": assigned_user.display_name if assigned_user else None,
        "remark": lead.remark,
        "contact_package_id": lead.contact_package_id,
        "last_action_at": lead.last_action_at.isoformat() if lead.last_action_at else None,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


def get_queue_stats(
    db: Session,
    tenant_id: int,
    account_id: Optional[int] = None,
    current_user_id: Optional[int] = None,
) -> dict:
    """各队列计数"""
    stats = {}
    for queue_key in QUEUE_FILTERS:
        q = _build_queue_query(db, tenant_id, queue_key, current_user_id)
        if account_id:
            q = q.filter(CommentLead.account_id == account_id)
        stats[queue_key] = q.count()
    return stats


def create_leads_from_comments(
    db: Session,
    tenant_id: int,
    account_id: int,
    comment_ids: list[int],
) -> int:
    """为新增评论创建线索（幂等，已存在的跳过）"""
    created = 0
    for cid in comment_ids:
        comment = db.query(WeChatComment).filter(WeChatComment.id == cid).first()
        if not comment:
            continue
        if not comment.openid:
            logger.warning("Comment %d has no openid, skipping lead creation", cid)
            continue

        existing = db.query(CommentLead).filter(
            CommentLead.account_id == account_id,
            CommentLead.comment_id == cid,
        ).first()
        if existing:
            continue

        lead = CommentLead(
            tenant_id=tenant_id,
            account_id=account_id,
            comment_id=cid,
            openid=comment.openid,
            status="pending_reply",
        )
        db.add(lead)
        created += 1

    if created:
        db.commit()
        logger.info("Created %d leads from comments for account %d", created, account_id)

    return created


async def public_reply(
    db: Session,
    tenant_id: int,
    lead_id: int,
    reply_type: str,
    content: str,
    operator_id: int,
    guide_keyword: Optional[str] = None,
    auto_send_on_message: bool = False,
    auto_send_package_id: Optional[int] = None,
) -> Optional[dict]:
    """公开回复评论（尝试同步到微信失败时仅保存本地）"""
    from app.services.wechat_comment_service import _get_service

    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == tenant_id,
    ).first()
    if not lead:
        return None

    comment = db.query(WeChatComment).filter(WeChatComment.id == lead.comment_id).first()
    if not comment:
        return None

    # 1. 尝试调用微信 API 回复（失败不阻断，本地先保存）
    wechat_synced = False
    wechat_error = None
    try:
        svc = await _get_service(db, lead.account_id)
        await svc.reply_comment(
            msg_data_id=comment.msg_id,
            comment_id=comment.comment_id,
            content=content,
        )
        wechat_synced = True
    except RuntimeError as e:
        wechat_error = str(e)
        logger.warning("WeChat reply failed for comment %s, saved locally: %s", comment.comment_id, e)

    # 2. 更新本地状态（无论微信是否成功都保存）
    now = datetime.now(timezone.utc)
    lead.reply_type = reply_type
    lead.reply_content = content
    lead.replied_at = now
    lead.last_action_at = now
    if reply_type == "guide":
        lead.status = "awaiting_user"
        if guide_keyword:
            from app.services.wechat_callback_service import normalize_keyword
            lead.guide_keyword = guide_keyword
            lead.guide_keyword_normalized = normalize_keyword(guide_keyword)
            lead.guide_sent_at = now
            lead.auto_send_on_message = auto_send_on_message
            lead.auto_send_package_id = auto_send_package_id
    elif lead.status == "pending_reply":
        lead.status = "pending_reply"

    comment.reply_content = content
    comment.reply_create_time = now
    if comment.status == "pending":
        comment.status = "replied"

    db.commit()
    db.refresh(lead)

    return {
        "id": lead.id,
        "reply_type": lead.reply_type,
        "reply_content": lead.reply_content,
        "replied_at": lead.replied_at.isoformat() if lead.replied_at else None,
        "lead_status": lead.status,
        "wechat_synced": wechat_synced,
        "wechat_error": wechat_error,
    }

    return {
        "id": lead.id,
        "reply_type": lead.reply_type,
        "reply_content": lead.reply_content,
        "replied_at": lead.replied_at.isoformat() if lead.replied_at else None,
        "lead_status": lead.status,
    }


def generate_reply_content(
    intent_type: Optional[str],
    keyword: str = "详情",
) -> str:
    """根据意图生成回复内容（V1 模板，V2 接 AI）"""
    template = REPLY_TEMPLATES.get(intent_type or "default", REPLY_TEMPLATES["default"])
    return template.format(keyword=keyword)


def create_sync_job(
    db: Session,
    tenant_id: int,
    account_id: int,
    scope: str = "all",
    article_id: Optional[int] = None,
) -> SyncJob:
    """创建同步任务记录"""
    job = SyncJob(
        tenant_id=tenant_id,
        account_id=account_id,
        job_type="sync_comments",
        scope=scope,
        article_id=article_id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_sync_job(
    db: Session,
    job_id: int,
    status: str,
    result: Optional[dict] = None,
    error_message: Optional[str] = None,
    celery_task_id: Optional[str] = None,
) -> Optional[SyncJob]:
    """更新同步任务状态"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        return None
    job.status = status
    if result:
        job.result = result
    if error_message:
        job.error_message = error_message
    if celery_task_id:
        job.celery_task_id = celery_task_id
    if status in ("running",):
        job.started_at = datetime.now(timezone.utc)
    if status in ("completed", "failed"):
        job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def get_sync_job(db: Session, tenant_id: int, job_id: int) -> Optional[dict]:
    """查询同步任务状态"""
    job = db.query(SyncJob).filter(
        SyncJob.id == job_id,
        SyncJob.tenant_id == tenant_id,
    ).first()
    if not job:
        return None
    return {
        "id": job.id,
        "account_id": job.account_id,
        "job_type": job.job_type,
        "scope": job.scope,
        "status": job.status,
        "result": job.result,
        "error_message": job.error_message,
        "celery_task_id": job.celery_task_id,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
