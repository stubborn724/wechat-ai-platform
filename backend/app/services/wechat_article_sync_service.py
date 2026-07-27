"""微信文章同步服务 — 拉取公众号草稿箱 & 已发布文章，同步到本地"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import AccountCredential, WeChatAccount, WeChatSyncedArticle
from app.services.encryption_service import derive_key, decrypt_secret

logger = logging.getLogger(__name__)

_BASE = "https://api.weixin.qq.com"


class WeChatArticleSyncService:
    """微信文章同步 API 封装"""

    def __init__(self, access_token: str):
        self.access_token = access_token

    # ------------------------------------------------------------------
    # 草稿箱
    # ------------------------------------------------------------------

    async def list_drafts(self, offset: int = 0, count: int = 20) -> dict:
        """获取草稿箱列表"""
        return await self._post("/cgi-bin/draft/batchget", {
            "offset": offset,
            "count": count,
            "no_content": 1,
        })

    async def get_draft_detail(self, media_id: str) -> dict:
        """获取单篇草稿详情（含正文）"""
        return await self._post("/cgi-bin/draft/get", {
            "media_id": media_id,
        })

    # ------------------------------------------------------------------
    # 已发布
    # ------------------------------------------------------------------

    async def list_published(self, offset: int = 0, count: int = 20) -> dict:
        """获取已发布文章列表"""
        return await self._post("/cgi-bin/freepublish/batchget", {
            "offset": offset,
            "count": count,
            "no_content": 1,
        })

    async def get_published_detail(self, article_id: str) -> dict:
        """获取单篇已发布文章详情（含正文）"""
        return await self._post("/cgi-bin/freepublish/get", {
            "article_id": article_id,
        })

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


# ============================================================================
# 高层业务函数
# ============================================================================


async def _get_token(db: Session, account_id: int, tenant_id: int = 0) -> str:
    """获取公众号 access_token（可选验证租户归属）"""
    account_query = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    )
    if tenant_id:
        account_query = account_query.filter(WeChatAccount.tenant_id == tenant_id)
    account = account_query.first()
    if not account:
        raise RuntimeError(f"Account {account_id} not found")
    cred_query = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    )
    if tenant_id:
        cred_query = cred_query.filter(AccountCredential.tenant_id == tenant_id)
    cred = cred_query.first()
    if not cred:
        raise RuntimeError(f"Credential for account {account_id} not found")
    key = derive_key(settings.credential_key)
    app_secret = decrypt_secret(cred.encrypted_secret, key)
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
    return token


def _make_svc(db: Session, account_id: int, tenant_id: int = 0) -> "WeChatArticleSyncService":
    """同步版获取 service（不依赖 asyncio 上下文）"""
    account_query = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    )
    if tenant_id:
        account_query = account_query.filter(WeChatAccount.tenant_id == tenant_id)
    account = account_query.first()
    if not account:
        raise RuntimeError(f"Account {account_id} not found")
    cred_query = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    )
    if tenant_id:
        cred_query = cred_query.filter(AccountCredential.tenant_id == tenant_id)
    cred = cred_query.first()
    if not cred:
        raise RuntimeError(f"Credential for account {account_id} not found")
    key = derive_key(settings.credential_key)
    app_secret = decrypt_secret(cred.encrypted_secret, key)

    import requests
    resp = requests.get(
        "https://api.weixin.qq.com/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": account.app_id,
            "secret": app_secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Failed to get access_token: {data.get('errmsg', data)}")
    return WeChatArticleSyncService(token)


async def sync_drafts(db: Session, tenant_id: int, account_id: int) -> dict:
    """同步草稿箱文章到本地"""
    token = await _get_token(db, account_id, tenant_id=tenant_id)
    svc = WeChatArticleSyncService(token)

    all_items = []
    offset = 0
    count = 20
    while True:
        data = await svc.list_drafts(offset=offset, count=count)
        items = data.get("item", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < count:
            break
        offset += count

    now = datetime.now(timezone.utc)
    synced = 0
    for item in all_items:
        media_id = item.get("media_id", "")
        content_info = item.get("content", {})
        article_item = content_info.get("news_item", [{}])[0] if content_info else {}

        if not media_id:
            continue

        title = article_item.get("title", "")
        if not title:
            continue

        # upsert
        existing = db.query(WeChatSyncedArticle).filter(
            WeChatSyncedArticle.account_id == account_id,
            WeChatSyncedArticle.article_type == "draft",
            WeChatSyncedArticle.media_id == media_id,
        ).first()

        if existing:
            existing.title = title
            existing.author = article_item.get("author", existing.author)
            existing.digest = article_item.get("digest", existing.digest)
            existing.cover_url = article_item.get("thumb_url", existing.cover_url)
            existing.raw_data = item
            existing.last_synced_at = now
            existing.is_deleted = False
        else:
            article = WeChatSyncedArticle(
                tenant_id=tenant_id,
                account_id=account_id,
                article_type="draft",
                media_id=media_id,
                title=title,
                author=article_item.get("author", ""),
                digest=article_item.get("digest", ""),
                cover_url=article_item.get("thumb_url", ""),
                need_open_comment=article_item.get("need_open_comment", 0),
                raw_data=item,
                last_synced_at=now,
            )
            db.add(article)
            synced += 1

    db.commit()
    logger.info("Synced %d drafts for account %d (total %d)", synced, account_id, len(all_items))
    return {"synced": synced, "total": len(all_items), "account_id": account_id, "type": "draft"}


async def sync_published(db: Session, tenant_id: int, account_id: int) -> dict:
    """同步已发布文章到本地"""
    token = await _get_token(db, account_id, tenant_id=tenant_id)
    svc = WeChatArticleSyncService(token)

    all_items = []
    offset = 0
    count = 20
    while True:
        data = await svc.list_published(offset=offset, count=count)
        items = data.get("item", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < count:
            break
        offset += count

    now = datetime.now(timezone.utc)
    synced = 0
    for item in all_items:
        article_id = item.get("article_id", "")
        content_info = item.get("content", {})
        news_item_list = content_info.get("news_item", []) if content_info else []
        if not article_id or not news_item_list:
            continue

        article_item = news_item_list[0]
        title = article_item.get("title", "")
        if not title:
            continue

        # 从 URL 提取 mid 作为 msg_data_id（评论 API 需要）
        article_url = article_item.get("link", article_item.get("url", ""))
        msg_data_id = ""
        if article_url:
            import re
            m = re.search(r'[?&]mid=(\d+)', article_url)
            if m:
                msg_data_id = m.group(1)

        publish_time = None
        ts = article_item.get("update_time", 0) or item.get("update_time", 0)
        if ts:
            publish_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)

        # upsert
        existing = db.query(WeChatSyncedArticle).filter(
            WeChatSyncedArticle.account_id == account_id,
            WeChatSyncedArticle.article_type == "published",
            WeChatSyncedArticle.wechat_article_id == article_id,
        ).first()

        if existing:
            existing.title = title
            existing.author = article_item.get("author", existing.author)
            existing.digest = article_item.get("digest", existing.digest)
            existing.cover_url = article_item.get("thumb_url", existing.cover_url)
            existing.wechat_url = article_url
            existing.msg_data_id = msg_data_id or existing.msg_data_id
            existing.publish_time = publish_time
            existing.raw_data = item
            existing.last_synced_at = now
            existing.is_deleted = False
        else:
            article = WeChatSyncedArticle(
                tenant_id=tenant_id,
                account_id=account_id,
                article_type="published",
                media_id=item.get("media_id", ""),
                wechat_article_id=article_id,
                msg_data_id=msg_data_id,
                title=title,
                author=article_item.get("author", ""),
                digest=article_item.get("digest", ""),
                cover_url=article_item.get("thumb_url", ""),
                wechat_url=article_url,
                publish_time=publish_time,
                need_open_comment=article_item.get("need_open_comment", 0),
                raw_data=item,
                last_synced_at=now,
            )
            db.add(article)
            synced += 1

    db.commit()
    logger.info("Synced %d published articles for account %d (total %d)", synced, account_id, len(all_items))
    return {"synced": synced, "total": len(all_items), "account_id": account_id, "type": "published"}


def get_local_articles(
    db: Session,
    tenant_id: int,
    account_id: Optional[int] = None,
    article_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[WeChatSyncedArticle], int]:
    """查询本地已同步的文章"""
    query = db.query(WeChatSyncedArticle).filter(
        WeChatSyncedArticle.tenant_id == tenant_id,
        WeChatSyncedArticle.is_deleted == False,
    )
    if account_id:
        query = query.filter(WeChatSyncedArticle.account_id == account_id)
    if article_type:
        query = query.filter(WeChatSyncedArticle.article_type == article_type)

    total = query.count()
    items = (
        query.order_by(WeChatSyncedArticle.publish_time.desc(),
                       WeChatSyncedArticle.last_synced_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


async def get_article_detail(db: Session, article_id: int, fetch_content: bool = False) -> Optional[WeChatSyncedArticle]:
    """获取文章详情，可选实时拉取正文"""
    article = db.query(WeChatSyncedArticle).filter(
        WeChatSyncedArticle.id == article_id,
        WeChatSyncedArticle.is_deleted == False,
    ).first()
    if not article:
        return None

    if fetch_content and not article.content:
        try:
            token = await _get_token(db, article.account_id)
            svc = WeChatArticleSyncService(token)
            if article.article_type == "draft" and article.media_id:
                detail = await svc.get_draft_detail(article.media_id)
                news_items = detail.get("news_item", [])
                if news_items:
                    article.content = news_items[0].get("content", "")
                    article.last_synced_at = datetime.now(timezone.utc)
                    db.commit()
            elif article.article_type == "published" and article.wechat_article_id:
                detail = await svc.get_published_detail(article.wechat_article_id)
                news_items = detail.get("news_item", [])
                if news_items:
                    article.content = news_items[0].get("content", "")
                    article.last_synced_at = datetime.now(timezone.utc)
                    db.commit()
        except Exception as e:
            logger.warning("Failed to fetch content for article %d: %s", article_id, e)

    return article
