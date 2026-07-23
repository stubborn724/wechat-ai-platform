"""微信 API token 获取 — 使用 AppID + AppSecret 方式（替代 OAuth 扫码授权）

为保持向后兼容，提供与 OAuth 版本相同签名的函数：
  - get_valid_token(db, account_id) -> str
  - get_valid_token_sync(db, account_id) -> str

但现在内部使用 WeChatAccount + AccountCredential 获取 access_token。
"""
import asyncio
import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import AccountCredential, WeChatAccount

logger = logging.getLogger(__name__)


async def get_valid_token(db: Session, account_id: int) -> str:
    """通过 AppID + AppSecret 获取 access_token"""
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    cred = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    ).first()
    if not cred:
        raise ValueError(f"Credential for account {account_id} not found")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": account.app_id,
                "secret": cred.encrypted_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token", "")
    if not token:
        raise ValueError(f"Failed to get access_token: {data}")

    logger.info("Got access_token for account %s (expires_in=%s)", account.name, data.get("expires_in"))
    return token


def get_valid_token_sync(db: Session, account_id: int) -> str:
    """同步版本的 get_valid_token"""
    import requests as _req

    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    cred = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    ).first()
    if not cred:
        raise ValueError(f"Credential for account {account_id} not found")

    resp = _req.get(
        "https://api.weixin.qq.com/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": account.app_id,
            "secret": cred.encrypted_secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token", "")
    if not token:
        raise ValueError(f"Failed to get access_token: {data}")

    logger.info("Got access_token for account %s (expires_in=%s)", account.name, data.get("expires_in"))
    return token
