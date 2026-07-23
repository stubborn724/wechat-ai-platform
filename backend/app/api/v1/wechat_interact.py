"""微信互动管理 — 评论回复 + 主动私信 + 自动配置"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
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
