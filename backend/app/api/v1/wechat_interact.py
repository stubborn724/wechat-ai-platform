"""微信互动管理 — 评论回复 + 主动私信 + 自动配置"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth, require_admin
from app.models.mysql_models import Article as ArticleModel, WeChatComment, WeChatMessage, WeChatCommentAutoConfig

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================


class SyncCommentsRequest(BaseModel):
    account_id: int
    msg_data_id: str


class ReplyCommentRequest(BaseModel):
    account_id: int
    comment_id: int
    msg_data_id: str
    content: str


class ToggleFavoriteRequest(BaseModel):
    account_id: int
    comment_id: int
    msg_data_id: str
    favorited: bool = True


class CommentResponse(BaseModel):
    id: int
    account_id: Optional[int] = None
    article_id: Optional[int] = None
    msg_id: str
    comment_id: str
    openid: Optional[str] = None
    nickname: Optional[str] = None
    content: str
    create_time: Optional[datetime] = None
    reply_content: Optional[str] = None
    reply_create_time: Optional[datetime] = None
    is_favorited: bool
    status: str

    model_config = {"from_attributes": True}


class CommentListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[CommentResponse]


class AutoConfigResponse(BaseModel):
    id: int
    account_id: int
    auto_reply_enabled: bool
    auto_reply_content: Optional[str] = None
    auto_msg_enabled: bool
    auto_msg_content: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateAutoConfigRequest(BaseModel):
    auto_reply_enabled: Optional[bool] = None
    auto_reply_content: Optional[str] = None
    auto_msg_enabled: Optional[bool] = None
    auto_msg_content: Optional[str] = None


class SendTextMessageRequest(BaseModel):
    account_id: int
    openid: str
    text: str


class SendImageMessageRequest(BaseModel):
    account_id: int
    openid: str
    media_id: str
    media_url: Optional[str] = None


class SendContactRequest(BaseModel):
    account_id: int
    openid: str
    contact_text: str
    qr_code_media_id: str


class MessageResponse(BaseModel):
    id: int
    account_id: Optional[int] = None
    openid: str
    msg_type: str
    content: Optional[str] = None
    media_id: Optional[str] = None
    media_url: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[MessageResponse]


# ============================================================================
# 评论管理
# ============================================================================


@router.get("/comments", response_model=CommentListResponse)
def list_comments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    article_id: Optional[int] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    query = db.query(WeChatComment).filter(
        WeChatComment.tenant_id == principal.tenant_id,
    )
    if status:
        query = query.filter(WeChatComment.status == status)
    if account_id:
        query = query.filter(WeChatComment.account_id == account_id)
    if article_id:
        query = query.filter(WeChatComment.article_id == article_id)

    total = query.count()
    items = (
        query.order_by(WeChatComment.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CommentListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/comments/{comment_id}", response_model=CommentResponse)
def get_comment(
    comment_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    comment = db.query(WeChatComment).filter(
        WeChatComment.id == comment_id,
        WeChatComment.tenant_id == principal.tenant_id,
    ).first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    return comment


@router.post("/comments/sync")
async def sync_comments(
    req: SyncCommentsRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """从微信同步某篇文章的评论到本地（含自动回复 & 自动私信）"""
    from app.services.wechat_comment_service import sync_comments_with_auto as _sync

    try:
        result = await _sync(db, principal.tenant_id, req.account_id, req.msg_data_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.post("/comments/sync-by-article")
async def sync_comments_by_article(
    article_id: int = Query(..., description="本地文章 ID"),
    account_id: int = Query(..., description="公众号 ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """通过本地文章 ID 同步评论（含自动回复 & 自动私信）"""
    from app.services.wechat_comment_service import sync_comments_with_auto as _sync

    article = db.query(ArticleModel).filter(
        ArticleModel.id == article_id,
        ArticleModel.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if not article.msg_data_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Article has no msg_data_id. Publish the article or set msg_data_id first.",
        )

    try:
        result = await _sync(db, principal.tenant_id, account_id, article.msg_data_id)
        if result.get("new", 0) > 0:
            db.query(WeChatComment).filter(
                WeChatComment.msg_id == article.msg_data_id,
                WeChatComment.article_id.is_(None),
            ).update({"article_id": article.id})
            db.commit()
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.get("/comments/debug-wechat-api")
async def debug_wechat_comment_api(
    article_id: int = Query(..., description="本地文章 ID"),
    account_id: int = Query(..., description="公众号 ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """调试：直接调微信评论 API 返回原始数据"""
    from app.services.wechat_comment_service import _get_service

    article = db.query(ArticleModel).filter(
        ArticleModel.id == article_id,
        ArticleModel.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if not article.msg_data_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Article has no msg_data_id")

    try:
        svc = await _get_service(db, account_id)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    raw = None
    open_result = None
    error = None

    # 1) 先尝试打开评论
    try:
        open_result = await svc._post("/cgi-bin/comment/open", {
            "msg_data_id": article.msg_data_id, "index": 0,
        })
    except RuntimeError as e:
        open_result = {"error": str(e)}

    # 2) 拉取评论列表
    try:
        raw = await svc._post("/cgi-bin/comment/list", {
            "msg_data_id": article.msg_data_id,
            "index": 0, "begin": 0, "count": 50, "type": 0,
        })
    except RuntimeError as e:
        error = str(e)

    return {
        "error": error,
        "msg_data_id": article.msg_data_id,
        "article_id": article.id,
        "article_task_id": article.task_id,
        "account_id": account_id,
        "open_comment_result": open_result,
        "list_raw_response": raw,
        "total": raw.get("total", 0) if raw else 0,
        "comment_count": len(raw.get("comment", [])) if raw else 0,
    }


# ============================================================================
# 评论回复
# ============================================================================


@router.post("/comments/reply")
async def reply_comment(
    req: ReplyCommentRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """回复评论（同步到微信 + 本地）"""
    from app.services.wechat_comment_service import reply_comment as _reply

    try:
        result = await _reply(db, req.account_id, req.comment_id, req.content, req.msg_data_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.post("/comments/toggle-favorite")
async def toggle_favorite(
    req: ToggleFavoriteRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """设置/取消精选评论"""
    from app.services.wechat_comment_service import mark_comment_favorite

    try:
        result = await mark_comment_favorite(
            db, req.account_id, req.comment_id, req.msg_data_id, req.favorited,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


# ============================================================================
# 自动回复 & 自动私信配置
# ============================================================================


@router.get("/comments/auto-config/{account_id}", response_model=AutoConfigResponse)
async def get_auto_config(
    account_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取公众号的自动回复/私信配置"""
    from app.services.wechat_comment_service import get_auto_config as _get

    config = await _get(db, principal.tenant_id, account_id)
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto-config not found")
    return config


@router.put("/comments/auto-config/{account_id}", response_model=AutoConfigResponse)
def update_auto_config(
    account_id: int,
    req: UpdateAutoConfigRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """创建或更新公众号的自动回复/私信配置"""
    from app.services.wechat_comment_service import update_auto_config as _update

    config = _update(
        db, principal.tenant_id, account_id,
        auto_reply_enabled=req.auto_reply_enabled,
        auto_reply_content=req.auto_reply_content,
        auto_msg_enabled=req.auto_msg_enabled,
        auto_msg_content=req.auto_msg_content,
    )
    return config


# ============================================================================
# 私信管理
# ============================================================================


@router.get("/messages", response_model=MessageListResponse)
def list_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    openid: Optional[str] = Query(None),
    msg_type: Optional[str] = Query(None),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    query = db.query(WeChatMessage).filter(
        WeChatMessage.tenant_id == principal.tenant_id,
    )
    if openid:
        query = query.filter(WeChatMessage.openid == openid)
    if msg_type:
        query = query.filter(WeChatMessage.msg_type == msg_type)
    if account_id:
        query = query.filter(WeChatMessage.account_id == account_id)

    total = query.count()
    items = (
        query.order_by(WeChatMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return MessageListResponse(total=total, page=page, page_size=page_size, items=items)


@router.post("/messages/send-text")
async def send_text_message(
    req: SendTextMessageRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """发送文本私信"""
    from app.services.wechat_message_service import send_text_message as _send

    try:
        result = await _send(db, principal.tenant_id, req.account_id, req.openid, req.text)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.post("/messages/send-image")
async def send_image_message(
    req: SendImageMessageRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """发送图片私信"""
    from app.services.wechat_message_service import send_image_message as _send

    try:
        result = await _send(db, principal.tenant_id, req.account_id, req.openid, req.media_id, req.media_url)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


@router.post("/messages/send-contact")
async def send_contact(
    req: SendContactRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """发送联系方式 + 二维码（文本+图片分两次发送）"""
    from app.services.wechat_message_service import send_contact_card

    try:
        result = await send_contact_card(
            db, principal.tenant_id, req.account_id, req.openid,
            req.contact_text, req.qr_code_media_id,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


# ============================================================================
# 评论线索工作台 API（P0）
# ============================================================================


class LeadListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


class PublicReplyRequest(BaseModel):
    reply_type: str = "normal"  # normal / guide
    content: str
    guide_keyword: Optional[str] = None
    auto_send_on_message: bool = False
    auto_send_package_id: Optional[int] = None


class GenerateReplyRequest(BaseModel):
    reply_type: str = "normal"  # normal / guide
    keyword: str = "详情"
    guide_keyword: Optional[str] = None
    auto_send_on_message: bool = False
    auto_send_package_id: Optional[int] = None


class SyncLeadsRequest(BaseModel):
    account_id: int
    scope: str = "all"  # all / article
    article_id: Optional[int] = None


@router.get("/leads/queue-stats")
def lead_queue_stats(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """各队列计数"""
    from app.services.wechat_lead_service import get_queue_stats

    return get_queue_stats(
        db, principal.tenant_id,
        account_id=account_id,
        current_user_id=principal.user_id,
    )


@router.get("/leads")
def list_leads(
    queue: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    account_id: Optional[int] = Query(None),
    intent_type: Optional[str] = Query(None),
    operator_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """线索列表（摘要）"""
    from app.services.wechat_lead_service import list_leads as _list

    items, total = _list(
        db, principal.tenant_id,
        queue=queue, page=page, page_size=page_size,
        account_id=account_id, intent_type=intent_type,
        operator_id=operator_id, keyword=keyword,
        current_user_id=principal.user_id,
    )
    return LeadListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/leads/{lead_id}")
def get_lead(
    lead_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """线索详情"""
    from app.services.wechat_lead_service import get_lead as _get

    data = _get(db, principal.tenant_id, lead_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return data


@router.post("/leads/sync")
async def sync_leads(
    req: SyncLeadsRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """同步评论并创建线索（后台线程执行，返回 job_id 供轮询）"""
    import threading
    from app.services.wechat_lead_service import create_sync_job, update_sync_job

    job = create_sync_job(
        db, principal.tenant_id, req.account_id,
        scope=req.scope, article_id=req.article_id,
    )

    # 后台线程执行同步，不依赖 Celery
    t = threading.Thread(
        target=_run_sync_background,
        args=(job.id, principal.tenant_id, req.account_id, req.scope, req.article_id),
        daemon=True,
    )
    t.start()

    return {"job_id": job.id, "status": "pending"}


@router.get("/leads/sync-jobs/{job_id}")
def get_sync_job_status(
    job_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """查询同步任务状态"""
    from app.services.wechat_lead_service import get_sync_job

    data = get_sync_job(db, principal.tenant_id, job_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync job not found")
    return data


@router.post("/leads/{lead_id}/public-reply")
async def public_reply(
    lead_id: int,
    req: PublicReplyRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """公开回复评论（优先同步微信，失败时报错但已保存本地）"""
    from app.services.wechat_lead_service import public_reply as _reply

    result = await _reply(
        db, principal.tenant_id, lead_id, req.reply_type, req.content, principal.user_id,
        guide_keyword=req.guide_keyword,
        auto_send_on_message=req.auto_send_on_message,
        auto_send_package_id=req.auto_send_package_id,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return result


@router.post("/leads/{lead_id}/generate-reply")
def generate_reply(
    lead_id: int,
    req: GenerateReplyRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """生成回复内容（V1 模板，V2 接 AI）"""
    from app.services.wechat_lead_service import get_lead, generate_reply_content

    lead = get_lead(db, principal.tenant_id, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    content = generate_reply_content(lead.get("intent_type"), keyword=req.keyword)
    return {"content": content, "intent_type": lead.get("intent_type")}


@router.post("/leads/{lead_id}/close")
def close_lead(
    lead_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """关闭线索"""
    from app.models.mysql_models import CommentLead as CommentLeadModel

    lead = db.query(CommentLeadModel).filter(
        CommentLeadModel.id == lead_id,
        CommentLeadModel.tenant_id == principal.tenant_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead.status = "closed"
    lead.last_action_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": lead.id, "status": "closed"}


def _run_sync_background(job_id: int, tenant_id: int, account_id: int, scope: str, article_id: int = None):
    """后台线程执行：同步评论并创建线索（非 Celery 版本）"""
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    from app.database import MysqlSessionLocal
    from app.services.wechat_lead_service import update_sync_job

    db = MysqlSessionLocal()
    try:
        update_sync_job(db, job_id, "running")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_do_sync(tenant_id, account_id, scope, article_id))
        finally:
            loop.close()

        update_sync_job(db, job_id, "completed", result=result)
        logger.info("Sync job %d completed: %s", job_id, result)
    except Exception as exc:
        logger.error("Sync job %d failed: %s", job_id, exc)
        update_sync_job(db, job_id, "failed", error_message=str(exc))
    finally:
        db.close()


async def _do_sync(tenant_id: int, account_id: int, scope: str, article_id: int = None) -> dict:
    """执行同步并创建线索（async 实体）"""
    import logging
    logger = logging.getLogger(__name__)
    from app.database import MysqlSessionLocal
    from app.models.mysql_models import Article, WeChatComment, WeChatSyncedArticle
    from app.services.wechat_comment_service import _get_service

    db = MysqlSessionLocal()
    try:
        svc = await _get_service(db, account_id)

        # 收集需要同步的文章（本地 Article + 微信同步文章 WeChatSyncedArticle）
        article_sources = []  # [(msg_data_id, source_label)]

        if scope == "article" and article_id:
            # 单篇文章同步：查本地和微信两表
            local_art = db.query(Article).filter(
                Article.id == article_id,
                Article.msg_data_id.isnot(None),
                Article.msg_data_id != "",
            ).first()
            if local_art:
                article_sources.append((local_art.msg_data_id, f"local:{local_art.id}"))

            synced_art = db.query(WeChatSyncedArticle).filter(
                WeChatSyncedArticle.id == article_id,
                WeChatSyncedArticle.account_id == account_id,
                WeChatSyncedArticle.msg_data_id.isnot(None),
                WeChatSyncedArticle.msg_data_id != "",
            ).first()
            if synced_art:
                article_sources.append((synced_art.msg_data_id, f"wechat:{synced_art.id}"))
        else:
            # 全量同步：查本地已发布 + 微信已同步
            for art in db.query(Article).filter(
                Article.msg_data_id.isnot(None),
                Article.msg_data_id != "",
                Article.status == "published",
            ).all():
                article_sources.append((art.msg_data_id, f"local:{art.id}"))

            for art in db.query(WeChatSyncedArticle).filter(
                WeChatSyncedArticle.account_id == account_id,
                WeChatSyncedArticle.msg_data_id.isnot(None),
                WeChatSyncedArticle.msg_data_id != "",
            ).all():
                article_sources.append((art.msg_data_id, f"wechat:{art.id}"))

        synced_articles = 0
        total_new_comments = 0
        total_new_leads = 0

        for msg_data_id, source in article_sources:
            try:
                new_ids, new_count, _ = await svc.sync_comments_to_db_v2(
                    db, tenant_id, account_id, msg_data_id,
                )
                if new_count > 0:
                    total_new_comments += new_count

                    # 关联 article_id（从 source 标签提取）
                    if source.startswith("local:"):
                        local_id = int(source.split(":")[1])
                        db.query(WeChatComment).filter(
                            WeChatComment.msg_id == msg_data_id,
                            WeChatComment.article_id.is_(None),
                        ).update({"article_id": local_id})
                        db.commit()

                    # 创建线索
                    from app.models.mysql_models import CommentLead
                    from app.services.comment_auto_conversion_service import process_comment_leads_auto_conversion
                    from app.services.wechat_lead_service import create_leads_from_comments
                    created = create_leads_from_comments(db, tenant_id, account_id, new_ids)
                    total_new_leads += created

                    new_lead_ids = [
                        row[0]
                        for row in db.query(CommentLead.id)
                        .filter(
                            CommentLead.tenant_id == tenant_id,
                            CommentLead.account_id == account_id,
                            CommentLead.comment_id.in_(new_ids),
                        )
                        .all()
                    ]
                    if new_lead_ids:
                        await process_comment_leads_auto_conversion(db, tenant_id, new_lead_ids)

                synced_articles += 1
            except Exception as exc:
                logger.warning("Sync %s failed: %s", source, exc)
                continue

        # 回填：给已有评论但缺少 lead 的记录创建线索
        backfilled = _backfill_leads(db, tenant_id, account_id)
        if backfilled:
            total_new_leads += len(backfilled)
            from app.services.comment_auto_conversion_service import process_comment_leads_auto_conversion
            await process_comment_leads_auto_conversion(db, tenant_id, backfilled)
            logger.info("Backfilled %d leads for account %d", len(backfilled), account_id)

        return {
            "synced_articles": synced_articles,
            "new_comments": total_new_comments,
            "new_leads": total_new_leads,
        }
    finally:
        db.close()


def _backfill_leads(db, tenant_id: int, account_id: int) -> list[int]:
    """为已有评论但缺少 CommentLead 的记录创建线索，并返回新建 lead ID 列表。"""
    from app.models.mysql_models import CommentLead, WeChatComment

    # 找没有对应 lead 的评论（不限 openid，无 openid 的标注为 failed）
    comment_ids = (
        db.query(WeChatComment.id)
        .filter(
            WeChatComment.tenant_id == tenant_id,
            WeChatComment.account_id == account_id,
            ~db.query(CommentLead.id)
            .filter(
                CommentLead.account_id == account_id,
                CommentLead.comment_id == WeChatComment.id,
            )
            .exists(),
        )
        .all()
    )
    ids = [row[0] for row in comment_ids]
    if not ids:
        return []

    created: list[int] = []
    for cid in ids:
        comment = db.query(WeChatComment).filter(WeChatComment.id == cid).first()
        if not comment:
            continue
        has_openid = bool(comment.openid)
        lead = CommentLead(
            tenant_id=tenant_id,
            account_id=account_id,
            comment_id=cid,
            openid=comment.openid or "",
            status="failed" if not has_openid else "pending_reply",
        )
        db.add(lead)
        db.flush()
        created.append(lead.id)

    db.commit()
    return created


# ============================================================================
# 微信能力探针（仅超级管理员）
# ============================================================================


class ProbeWechatRequest(BaseModel):
    account_id: int
    openid: str


@router.post("/leads/_probe-wechat")
async def probe_wechat(
    req: ProbeWechatRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    db: Session = Depends(get_mysql_db),
):
    """测试指定公众号和用户的微信接口连通性（仅超级管理员）"""
    import httpx
    from app.config import settings
    from app.models.mysql_models import AccountCredential, WeChatAccount
    from app.services.encryption_service import derive_key, decrypt_secret

    result = {
        "account_id": req.account_id,
        "openid": req.openid,
        "token_ok": False,
        "user_info": None,
        "send_test": None,
        "conclusion": "UNKNOWN",
        "errors": [],
    }

    # 1. 获取 access_token
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == req.account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        result["errors"].append("ACCOUNT_NOT_FOUND")
        result["conclusion"] = "ACCOUNT_ERROR"
        return result

    cred = db.query(AccountCredential).filter(
        AccountCredential.account_id == req.account_id,
    ).first()
    if not cred:
        result["errors"].append("CREDENTIAL_NOT_FOUND")
        result["conclusion"] = "ACCOUNT_ERROR"
        return result

    key = derive_key(settings.credential_key)
    app_secret = decrypt_secret(cred.encrypted_secret, key)

    from app.services.wechat_gateway_policy import ensure_direct_wechat_api_allowed
    ensure_direct_wechat_api_allowed("微信互动调试")

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": account.app_id, "secret": app_secret},
        )
        token_data = token_resp.json()
        token = token_data.get("access_token", "")
        if not token:
            result["errors"].append(f"TOKEN_FAILED: {token_data.get('errmsg', 'unknown')}")
            result["conclusion"] = "ACCOUNT_ERROR"
            return result
        result["token_ok"] = True

        # 2. 查询用户信息
        user_resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/user/info",
            params={"access_token": token, "openid": req.openid, "lang": "zh_CN"},
        )
        user_data = user_resp.json()
        if user_data.get("errcode", 0) == 0:
            result["user_info"] = {
                "subscribe": user_data.get("subscribe", 0) == 1,
                "nickname": user_data.get("nickname", ""),
                "subscribe_time": user_data.get("subscribe_time"),
            }
        else:
            result["errors"].append(f"USER_INFO_FAILED: errcode={user_data.get('errcode')} {user_data.get('errmsg', '')}")
            result["user_info"] = None

        # 3. 发送测试消息
        test_resp = await client.post(
            "https://api.weixin.qq.com/cgi-bin/message/custom/send",
            params={"access_token": token},
            json={
                "touser": req.openid,
                "msgtype": "text",
                "text": {"content": "[探针测试] 这是一条来自系统的测试消息，请忽略。"},
            },
        )
        send_data = test_resp.json()
        send_ok = send_data.get("errcode", -1) == 0
        result["send_test"] = {
            "success": send_ok,
            "wechat_error_code": send_data.get("errcode") if not send_ok else None,
            "wechat_error_message": send_data.get("errmsg", "") if not send_ok else None,
        }
        if not send_ok:
            result["errors"].append(f"SEND_FAILED: errcode={send_data.get('errcode')} {send_data.get('errmsg', '')}")

    # 4. 综合判断
    is_following = result["user_info"] and result["user_info"]["subscribe"]
    send_failed_code = result["send_test"]["wechat_error_code"] if result["send_test"] else None

    if send_ok:
        result["conclusion"] = "ELIGIBLE"
    elif send_failed_code == 45015:
        result["conclusion"] = "NO_ACTIVE_SESSION"
    elif send_failed_code == 43004:
        result["conclusion"] = "USER_NOT_FOLLOWING"
    elif send_failed_code in (48001, 40001):
        result["conclusion"] = "ACCOUNT_PERMISSION_MISSING"
    elif not is_following:
        result["conclusion"] = "USER_NOT_FOLLOWING"
    else:
        result["conclusion"] = "UNKNOWN"

    return result


# ============================================================================
# P1.1 联系资料包 CRUD
# ============================================================================


class CreatePackageRequest(BaseModel):
    account_id: int
    name: str
    description: Optional[str] = None
    contact_name: Optional[str] = None
    wechat_id: Optional[str] = None
    phone: Optional[str] = None
    text_content: Optional[str] = None
    qr_asset_id: Optional[int] = None
    is_default: bool = False
    is_enabled: bool = False


class UpdatePackageRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contact_name: Optional[str] = None
    wechat_id: Optional[str] = None
    phone: Optional[str] = None
    text_content: Optional[str] = None
    qr_asset_id: Optional[int] = None
    is_default: Optional[bool] = None
    is_enabled: Optional[bool] = None


@router.get("/contact-packages")
def list_contact_packages(
    account_id: Optional[int] = Query(None),
    enabled: bool = Query(False),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import list_packages
    items, total = list_packages(
        db, principal.tenant_id,
        account_id=account_id, enabled_only=enabled,
        keyword=keyword, page=page, page_size=page_size,
    )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/contact-packages/{pkg_id}")
def get_contact_package(
    pkg_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import get_package
    data = get_package(db, principal.tenant_id, pkg_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    return data


@router.post("/contact-packages", status_code=status.HTTP_201_CREATED)
def create_contact_package(
    req: CreatePackageRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import create_package
    try:
        pkg = create_package(db, principal.tenant_id, req.account_id, req.model_dump(), principal.user_id)
        return {"id": pkg.id, "name": pkg.name}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/contact-packages/{pkg_id}")
def update_contact_package(
    pkg_id: int,
    req: UpdatePackageRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import update_package
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        pkg = update_package(db, principal.tenant_id, pkg_id, data)
        if not pkg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
        return {"id": pkg.id, "name": pkg.name}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/contact-packages/{pkg_id}/enable")
def enable_contact_package(
    pkg_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import enable_package
    try:
        pkg = enable_package(db, principal.tenant_id, pkg_id)
        if not pkg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
        return {"id": pkg.id, "is_enabled": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/contact-packages/{pkg_id}/disable")
def disable_contact_package(
    pkg_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import disable_package
    pkg = disable_package(db, principal.tenant_id, pkg_id)
    if not pkg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    return {"id": pkg.id, "is_enabled": False}


@router.delete("/contact-packages/{pkg_id}")
def delete_contact_package(
    pkg_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_contact_package_service import soft_delete_package
    try:
        ok = soft_delete_package(db, principal.tenant_id, pkg_id)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
        return {"deleted": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================================
# P1.2 微信素材管理
# ============================================================================


class UploadMediaRequest(BaseModel):
    account_id: int
    asset_id: int


@router.post("/media-assets/upload")
async def upload_media_asset(
    req: UploadMediaRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_media_service import get_or_prepare_image_media
    try:
        result = await get_or_prepare_image_media(
            db, principal.tenant_id, req.account_id, req.asset_id, force_refresh=True,
        )
        return {
            "id": result.id,
            "media_id": result.media_id,
            "status": result.status,
            "is_mock": result.is_mock,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/media-assets/{media_id}")
def get_media_asset(
    media_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_media_service import get_media_asset
    data = get_media_asset(db, principal.tenant_id, media_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    return data


@router.post("/media-assets/{media_id}/refresh")
async def refresh_media_asset(
    media_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_media_service import refresh_media
    data = await refresh_media(db, principal.tenant_id, media_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    return data


# ============================================================================
# P1.3 三态资格检查
# ============================================================================


@router.post("/leads/{lead_id}/check-eligibility")
async def check_lead_eligibility(
    lead_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_eligibility_service import (
        check_contact_eligibility, cache_eligibility,
    )
    from app.models.mysql_models import CommentLead

    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == principal.tenant_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    try:
        result = await check_contact_eligibility(
            db, principal.tenant_id, lead.account_id, lead.openid, force_refresh=True,
        )
        cache_eligibility(db, lead_id, result, principal.tenant_id)
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


# ============================================================================
# P1.4 ContactDelivery 发送任务
# ============================================================================


class CreateDeliveryRequest(BaseModel):
    package_id: int
    idempotency_key: str


@router.post("/leads/{lead_id}/deliveries", status_code=status.HTTP_201_CREATED)
async def create_lead_delivery(
    lead_id: int,
    req: CreateDeliveryRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    import threading
    from app.services.wechat_delivery_service import create_delivery, execute_delivery

    from app.models.mysql_models import CommentLead
    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == principal.tenant_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    try:
        delivery = create_delivery(
            db, principal.tenant_id, lead_id, lead.account_id, lead.openid,
            req.package_id, principal.user_id, req.idempotency_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 异步执行
    t = threading.Thread(target=_run_delivery, args=(delivery.id,), daemon=True)
    t.start()

    return {"delivery_id": delivery.id, "status": "pending"}


@router.get("/deliveries/{delivery_id}")
def get_delivery_status(
    delivery_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_delivery_service import get_delivery
    data = get_delivery(db, principal.tenant_id, delivery_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")
    return data


@router.get("/leads/{lead_id}/deliveries")
def list_lead_deliveries(
    lead_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_delivery_service import list_deliveries_by_lead
    return list_deliveries_by_lead(db, principal.tenant_id, lead_id)


# ============================================================================
# P1.5 分步骤重试
# ============================================================================


class RetryDeliveryRequest(BaseModel):
    step: str  # text / qr / all
    idempotency_key: str


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery_endpoint(
    delivery_id: int,
    req: RetryDeliveryRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    from app.services.wechat_delivery_service import retry_delivery as _retry

    try:
        result = await _retry(
            db, principal.tenant_id, delivery_id,
            req.step, req.idempotency_key, operator_id=principal.user_id,
        )
        from app.services.wechat_delivery_service import get_delivery
        return get_delivery(db, principal.tenant_id, result.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================================
# 异步执行辅助（后台线程）
# ============================================================================


def _run_delivery(delivery_id: int):
    """后台线程执行发送"""
    import asyncio
    from app.services.wechat_delivery_service import execute_delivery
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(execute_delivery(delivery_id))
    finally:
        loop.close()


# ============================================================================
# P2 微信回调（不经过认证，account_key 为公开标识）
# ============================================================================


@router.get("/wechat/callback/{callback_key}")
def wechat_callback_verify(
    callback_key: str,
    signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
    db: Session = Depends(get_mysql_db),
):
    """微信 URL 验证（GET 请求）"""
    from app.services.wechat_callback_service import find_account_by_key, verify_signature

    account = find_account_by_key(db, callback_key)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    token = account.callback_token or ""
    if not verify_signature(token, timestamp, nonce, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature verification failed")

    return Response(content=echostr, media_type="text/plain")


@router.post("/wechat/callback/{callback_key}")
async def wechat_callback_receive(
    callback_key: str,
    signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    db: Session = Depends(get_mysql_db),
):
    """接收微信消息回调（POST 请求）"""
    from app.services.wechat_callback_service import (
        find_account_by_key, verify_signature, parse_xml, handle_callback_message,
    )

    # 查找公众号
    account = find_account_by_key(db, callback_key)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    # 签名校验
    token = account.callback_token or ""
    if not verify_signature(token, timestamp, nonce, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature verification failed")

    # 解析 XML
    body = await Request.body()
    xml_data = parse_xml(body)

    # 入库 + 去重
    result = handle_callback_message(db, account, xml_data)

    # 投递异步处理
    if not result.get("duplicate") and result.get("message_id"):
        _dispatch_message_processing(result["message_id"])

    # 快速返回 success（微信要求 5 秒内响应）
    return Response(content="success", media_type="text/plain")


def _dispatch_message_processing(message_id: int):
    """投递消息处理任务"""
    import threading
    from app.services.wechat_message_handler import process_incoming_message

    t = threading.Thread(target=process_incoming_message, args=(message_id,), daemon=True)
    t.start()
