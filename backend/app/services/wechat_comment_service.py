"""微信公众号评论管理服务 — 同步、回复、精选"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import WeChatAccount, WeChatComment, WeChatOAuthAccount

logger = logging.getLogger(__name__)

_BASE = "https://api.weixin.qq.com"


class WeChatCommentService:
    """微信评论 API 封装"""

    def __init__(self, access_token: str):
        self.access_token = access_token

    # ------------------------------------------------------------------
    # 评论开关
    # ------------------------------------------------------------------

    async def open_comment(self, msg_data_id: str, index: int = 0) -> dict:
        """打开文章评论"""
        return await self._post("/cgi-bin/comment/open", {
            "msg_data_id": msg_data_id,
            "index": index,
        })

    async def close_comment(self, msg_data_id: str, index: int = 0) -> dict:
        """关闭文章评论"""
        return await self._post("/cgi-bin/comment/close", {
            "msg_data_id": msg_data_id,
            "index": index,
        })

    # ------------------------------------------------------------------
    # 评论列表
    # ------------------------------------------------------------------

    async def list_comments(
        self,
        msg_data_id: str,
        index: int = 0,
        begin: int = 0,
        count: int = 50,
        comment_type: int = 0,
    ) -> dict:
        """获取文章评论列表

        Args:
            comment_type: 0=全部, 1=仅精选
        """
        return await self._post("/cgi-bin/comment/list", {
            "msg_data_id": msg_data_id,
            "index": index,
            "begin": begin,
            "count": count,
            "type": comment_type,
        })

    # ------------------------------------------------------------------
    # 回复 / 删除 / 精选
    # ------------------------------------------------------------------

    async def reply_comment(self, msg_data_id: str, comment_id: str, content: str, index: int = 0) -> dict:
        """回复评论"""
        return await self._post("/cgi-bin/comment/reply", {
            "msg_data_id": msg_data_id,
            "index": index,
            "comment_id": int(comment_id),
            "content": content,
        })

    async def delete_comment(self, msg_data_id: str, comment_id: str, index: int = 0) -> dict:
        """删除评论"""
        return await self._post("/cgi-bin/comment/delete", {
            "msg_data_id": msg_data_id,
            "index": index,
            "comment_id": int(comment_id),
        })

    async def mark_favorite(self, msg_data_id: str, comment_id: str, index: int = 0) -> dict:
        """标记为精选评论"""
        return await self._post("/cgi-bin/comment/markelect", {
            "msg_data_id": msg_data_id,
            "index": index,
            "comment_id": int(comment_id),
        })

    async def unmark_favorite(self, msg_data_id: str, comment_id: str, index: int = 0) -> dict:
        """取消精选"""
        return await self._post("/cgi-bin/comment/unmarkelect", {
            "msg_data_id": msg_data_id,
            "index": index,
            "comment_id": int(comment_id),
        })

    # ------------------------------------------------------------------
    # 同步评论到本地
    # ------------------------------------------------------------------

    async def sync_comments_to_db(
        self, db: Session, tenant_id: int, account_id: int, msg_data_id: str,
    ) -> Tuple[int, int]:
        """将微信后台评论同步到本地数据库

        自动先尝试打开评论（已打开则忽略错误）。
        然后拉取评论列表，逐条存入本地。

        Returns:
            (新增数, 总评论数)
        """
        # 先尝试打开评论（如果已打开，WeChat 会返回 errcode，忽略即可）
        try:
            await self.open_comment(msg_data_id)
        except RuntimeError:
            pass  # 可能已经打开了，忽略

        wechat_data = await self.list_comments(msg_data_id, count=50, comment_type=0)
        total_count = wechat_data.get("total", 0)
        comments = wechat_data.get("comment", [])

        new_count = 0
        for c in comments:
            comment_id = str(c.get("user_comment_id", c.get("comment_id", "")))
            if not comment_id:
                continue

            existing = db.query(WeChatComment).filter(
                WeChatComment.comment_id == comment_id,
            ).first()
            if existing:
                continue

            comment = WeChatComment(
                tenant_id=tenant_id,
                account_id=account_id,
                msg_id=msg_data_id,
                comment_id=comment_id,
                user_comment_id=str(c.get("user_comment_id", "")),
                openid=c.get("openid", ""),
                nickname=c.get("nickname", ""),
                content=c.get("content", ""),
                create_time=(
                    datetime.fromtimestamp(c["create_time"], tz=timezone.utc)
                    if c.get("create_time") else None
                ),
                is_favorited=c.get("comment_type", 0) == 1,
                status="pending",
            )
            db.add(comment)
            new_count += 1

        db.commit()
        logger.info("Synced %d new comments for msg_data_id=%s (total=%d)", new_count, msg_data_id, total_count)
        return new_count, total_count

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _post(self, path: str, data: dict) -> dict:
        url = f"{_BASE}{path}?access_token={self.access_token}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=data)
            resp.raise_for_status()
            result = resp.json()
        if result.get("errcode", 0) != 0:
            errmsg = result.get("errmsg", "unknown error")
            errcode = result.get("errcode", -1)
            logger.error("WeChat API error %d: %s (path=%s)", errcode, errmsg, path)
            raise RuntimeError(f"WeChat API error {errcode}: {errmsg}")
        return result

    def _post_sync(self, path: str, data: dict) -> dict:
        """同步版 _post，用于 Celery 等非异步上下文"""
        import requests as _requests
        url = f"{_BASE}{path}?access_token={self.access_token}"
        resp = _requests.post(url, json=data, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode", 0) != 0:
            errmsg = result.get("errmsg", "unknown error")
            errcode = result.get("errcode", -1)
            logger.error("WeChat API error %d: %s (path=%s)", errcode, errmsg, path)
            raise RuntimeError(f"WeChat API error {errcode}: {errmsg}")
        return result

    def list_comments_sync(self, msg_data_id: str, index: int = 0, begin: int = 0, count: int = 50, comment_type: int = 0) -> dict:
        """同步版 list_comments"""
        return self._post_sync("/cgi-bin/comment/list", {
            "msg_data_id": msg_data_id, "index": index,
            "begin": begin, "count": count, "type": comment_type,
        })

    def sync_comments_to_db_sync(self, db: Session, tenant_id: int, account_id: int, msg_data_id: str) -> Tuple[int, int]:
        """同步版 sync_comments_to_db"""
        wechat_data = self.list_comments_sync(msg_data_id, count=50, comment_type=0)
        total_count = wechat_data.get("total", 0)
        comments = wechat_data.get("comment", [])
        new_count = 0
        for c in comments:
            comment_id = str(c.get("user_comment_id", c.get("comment_id", "")))
            if not comment_id:
                continue
            existing = db.query(WeChatComment).filter(
                WeChatComment.comment_id == comment_id,
            ).first()
            if existing:
                continue
            comment = WeChatComment(
                tenant_id=tenant_id,
                account_id=account_id,
                msg_id=msg_data_id,
                comment_id=comment_id,
                user_comment_id=str(c.get("user_comment_id", "")),
                openid=c.get("openid", ""),
                nickname=c.get("nickname", ""),
                content=c.get("content", ""),
                create_time=(
                    datetime.fromtimestamp(c["create_time"], tz=timezone.utc)
                    if c.get("create_time") else None
                ),
                is_favorited=c.get("comment_type", 0) == 1,
                status="pending",
            )
            db.add(comment)
            new_count += 1
        db.commit()
        logger.info("Synced %d new comments for msg_data_id=%s (total=%d)", new_count, msg_data_id, total_count)
        return new_count, total_count


# ============================================================================
# 高层业务函数（供 API 路由调用）
# ============================================================================


async def _get_service(db: Session, account_id: int) -> "WeChatCommentService":
    """从账号获取 access_token（支持 OAuth 和普通凭据两种账号类型）"""
    from app.services.wechat_oauth_service import get_valid_token
    from app.models.mysql_models import WeChatOAuthAccount, AccountCredential, WeChatAccount
    import httpx

    # 先查 OAuth 账号
    oauth = db.query(WeChatOAuthAccount).filter(
        WeChatOAuthAccount.id == account_id,
        WeChatOAuthAccount.is_active == True,
    ).first()
    if oauth:
        token = await get_valid_token(db, account_id)
        return WeChatCommentService(token)

    # 再查普通账号（通过 app_id + app_secret 直接获取 access_token）
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if account:
        cred = db.query(AccountCredential).filter(
            AccountCredential.account_id == account_id,
        ).first()
        if not cred:
            raise RuntimeError(f"Account {account_id} has no credential configured")
        app_secret = cred.encrypted_secret
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": account.app_id,
                    "secret": app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError(f"Failed to get access_token: {data.get('errmsg', data)}")
        return WeChatCommentService(token)

    raise RuntimeError(f"Account {account_id} not found (neither OAuth nor regular)")


async def sync_comments(
    db: Session, tenant_id: int, account_id: int, msg_data_id: str,
) -> dict:
    """同步评论：获取公众号文章评论并存入本地"""
    svc = await _get_service(db, account_id)
    new, total = await svc.sync_comments_to_db(db, tenant_id, account_id, msg_data_id)
    return {"new": new, "total": total, "msg_data_id": msg_data_id}


async def reply_comment(
    db: Session,
    account_id: int,
    comment_id: int,
    content: str,
    msg_data_id: str,
) -> dict:
    """回复评论（同时回写到微信和本地）"""
    svc = await _get_service(db, account_id)
    result = await svc.reply_comment(msg_data_id, str(comment_id), content)

    # 更新本地记录
    local = db.query(WeChatComment).filter(
        WeChatComment.comment_id == str(comment_id),
    ).first()
    if local:
        local.reply_content = content
        local.reply_create_time = datetime.now(timezone.utc)
        local.status = "replied"
        db.commit()

    return result


async def mark_comment_favorite(
    db: Session,
    account_id: int,
    comment_id: int,
    msg_data_id: str,
    favorited: bool = True,
) -> dict:
    """设置/取消精选评论"""
    svc = await _get_service(db, account_id)
    if favorited:
        result = await svc.mark_favorite(msg_data_id, str(comment_id))
    else:
        result = await svc.unmark_favorite(msg_data_id, str(comment_id))

    local = db.query(WeChatComment).filter(
        WeChatComment.comment_id == str(comment_id),
    ).first()
    if local:
        local.is_favorited = favorited
        db.commit()

    return result
