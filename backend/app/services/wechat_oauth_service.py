"""微信开放平台第三方扫码授权服务

流程:
  1. 前端请求授权链接 → 后端生成预授权码 → 返回微信授权 URL
  2. 用户扫码确认 → 微信回调 callback URL → 后端收到 auth_code
  3. 后端用 auth_code 换取 authorizer_access_token / refresh_token
  4. 存储 token，后续通过 get_valid_token() 自动续期使用
"""

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import WeChatOAuthAccount

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 缓存（进程内）
# ---------------------------------------------------------------------------
_component_access_token: Optional[str] = None
_component_access_token_expires: float = 0

# ---------------------------------------------------------------------------
# 基础请求
# ---------------------------------------------------------------------------

_BASE = "https://api.weixin.qq.com"


async def _get_component_access_token() -> str:
    """获取第三方平台 component_access_token（自动缓存续期）"""
    global _component_access_token, _component_access_token_expires

    if _component_access_token and time.time() < _component_access_token_expires:
        return _component_access_token

    if not settings.wechat_component_app_id or not settings.wechat_component_app_secret:
        raise RuntimeError("WeChat Open Platform credentials not configured")

    # 需要 component_verify_ticket，这里从 DB 或缓存获取
    # 简化：直接从 settings 构造，生产环境应持久化 ticket
    ticket = _get_verify_ticket()
    if not ticket:
        raise RuntimeError("component_verify_ticket not available — WeChat hasn't pushed one yet")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/cgi-bin/component/api_component_token",
            json={
                "component_appid": settings.wechat_component_app_id,
                "component_appsecret": settings.wechat_component_app_secret,
                "component_verify_ticket": ticket,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("component_access_token", "")
    expires_in = data.get("expires_in", 600)
    _component_access_token = token
    _component_access_token_expires = time.time() + expires_in - 120  # 提前 2 分钟过期
    logger.info("Got component_access_token, expires in %ds", expires_in)
    return token


# ---------------------------------------------------------------------------
# Verify Ticket 存储（生产环境应存 DB / Redis）
# ---------------------------------------------------------------------------
_verify_ticket: str = ""


def set_verify_ticket(ticket: str):
    """收到微信推送的 component_verify_ticket 时调用"""
    global _verify_ticket
    _verify_ticket = ticket
    logger.info("component_verify_ticket updated")


def _get_verify_ticket() -> str:
    return _verify_ticket


# ---------------------------------------------------------------------------
# 步骤1: 生成授权链接
# ---------------------------------------------------------------------------


async def get_pre_auth_code() -> str:
    """获取预授权码（pre_auth_code）"""
    token = await _get_component_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/cgi-bin/component/api_create_preauthcode",
            params={"component_access_token": token},
            json={"component_appid": settings.wechat_component_app_id},
        )
        resp.raise_for_status()
        data = resp.json()
    code = data.get("pre_auth_code", "")
    logger.info("Got pre_auth_code: %s...", code[:10] if code else "")
    return code


def build_authorization_url(pre_auth_code: str) -> str:
    """构造微信扫码授权 URL"""
    app_id = settings.wechat_component_app_id
    callback = settings.wechat_oauth_callback_url
    return (
        f"https://mp.weixin.qq.com/cgi-bin/componentloginpage"
        f"?component_appid={app_id}"
        f"&pre_auth_code={pre_auth_code}"
        f"&redirect_uri={callback}"
    )


# ---------------------------------------------------------------------------
# 步骤3: 回调处理 — 用 auth_code 换取 authorizer_token
# ---------------------------------------------------------------------------


async def exchange_auth_code(auth_code: str) -> dict:
    """用授权回调的 auth_code 换取 authorizer_access_token 和 authorizer_refresh_token

    Returns:
        {
            "app_id": "wx...",
            "authorizer_access_token": "...",
            "authorizer_refresh_token": "...",
            "expires_in": 7200,
            "func_info": [...],
            "nick_name": "...",
            "head_img": "...",
            ...
        }
    """
    token = await _get_component_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/cgi-bin/component/api_query_auth",
            params={"component_access_token": token},
            json={
                "component_appid": settings.wechat_component_app_id,
                "authorization_code": auth_code,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    auth_info = data.get("authorization_info", {})
    result = {
        "app_id": auth_info.get("authorizer_appid", ""),
        "authorizer_access_token": auth_info.get("authorizer_access_token", ""),
        "authorizer_refresh_token": auth_info.get("authorizer_refresh_token", ""),
        "expires_in": auth_info.get("expires_in", 7200),
        "func_info": auth_info.get("func_info", []),
    }

    # 获取公众号基本信息
    await _fill_account_info(result)
    return result


async def _fill_account_info(result: dict):
    """查询授权公众号的详细信息（昵称、头像等）"""
    token = await _get_component_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/cgi-bin/component/api_get_authorizer_info",
            params={"component_access_token": token},
            json={
                "component_appid": settings.wechat_component_app_id,
                "authorizer_appid": result["app_id"],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    authorizer_info = data.get("authorizer_info", {})
    result["nick_name"] = authorizer_info.get("nick_name", "")
    result["head_img"] = authorizer_info.get("head_img", "")
    result["service_type_info"] = authorizer_info.get("service_type_info", {}).get("id")
    result["verify_type_info"] = authorizer_info.get("verify_type_info", {}).get("id")
    result["user_name"] = authorizer_info.get("user_name", "")
    result["alias"] = authorizer_info.get("alias", "")
    result["qrcode_url"] = authorizer_info.get("qrcode_url", "")
    result["business_info"] = authorizer_info.get("business_info", {})


# ---------------------------------------------------------------------------
# Token 续期
# ---------------------------------------------------------------------------


async def refresh_authorizer_token(app_id: str, refresh_token: str) -> Tuple[str, str, int]:
    """刷新 authorizer_access_token

    Returns:
        (new_access_token, new_refresh_token, expires_in)
    """
    token = await _get_component_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/cgi-bin/component/api_authorizer_token",
            params={"component_access_token": token},
            json={
                "component_appid": settings.wechat_component_app_id,
                "authorizer_appid": app_id,
                "authorizer_refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return (
        data.get("authorizer_access_token", ""),
        data.get("authorizer_refresh_token", refresh_token),
        data.get("expires_in", 7200),
    )


# ---------------------------------------------------------------------------
# 数据库操作
# ---------------------------------------------------------------------------


def save_authorized_account(db: Session, tenant_id: int, auth_data: dict) -> WeChatOAuthAccount:
    """保存或更新授权公众号信息"""
    app_id = auth_data["app_id"]
    account = db.query(WeChatOAuthAccount).filter(
        WeChatOAuthAccount.tenant_id == tenant_id,
        WeChatOAuthAccount.app_id == app_id,
    ).first()

    expires_at = datetime.now(timezone.utc).timestamp() + auth_data.get("expires_in", 7200)

    if account:
        account.authorizer_access_token = auth_data.get("authorizer_access_token", "")
        account.authorizer_refresh_token = auth_data.get("authorizer_refresh_token", "")
        account.token_expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        account.nick_name = auth_data.get("nick_name", account.nick_name)
        account.head_img = auth_data.get("head_img", account.head_img)
        account.func_info = auth_data.get("func_info", account.func_info)
        account.is_active = True
    else:
        account = WeChatOAuthAccount(
            tenant_id=tenant_id,
            app_id=app_id,
            nick_name=auth_data.get("nick_name", ""),
            head_img=auth_data.get("head_img", ""),
            service_type_info=auth_data.get("service_type_info"),
            verify_type_info=auth_data.get("verify_type_info"),
            user_name=auth_data.get("user_name", ""),
            alias=auth_data.get("alias", ""),
            qrcode_url=auth_data.get("qrcode_url", ""),
            business_info=auth_data.get("business_info"),
            authorizer_access_token=auth_data.get("authorizer_access_token", ""),
            authorizer_refresh_token=auth_data.get("authorizer_refresh_token", ""),
            token_expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            func_info=auth_data.get("func_info"),
            authorization_app_id=settings.wechat_component_app_id,
        )
        db.add(account)

    db.commit()
    db.refresh(account)
    logger.info("Saved authorized account: %s (%s)", account.nick_name, app_id)
    return account


async def get_valid_token(db: Session, account_id: int) -> str:
    """获取有效的 authorizer_access_token，自动续期"""
    account = db.query(WeChatOAuthAccount).filter(
        WeChatOAuthAccount.id == account_id,
        WeChatOAuthAccount.is_active == True,
    ).first()
    if not account:
        raise ValueError(f"OAuth account {account_id} not found")

    now = datetime.now(timezone.utc)
    if account.token_expires_at and account.token_expires_at > now:
        return account.authorizer_access_token

    # 需要续期
    if not account.authorizer_refresh_token:
        raise ValueError(f"OAuth account {account_id} has no refresh_token")

    new_access, new_refresh, expires_in = await refresh_authorizer_token(
        account.app_id, account.authorizer_refresh_token,
    )
    account.authorizer_access_token = new_access
    account.authorizer_refresh_token = new_refresh
    expires_at = datetime.fromtimestamp(now.timestamp() + expires_in, tz=timezone.utc)
    account.token_expires_at = expires_at
    db.commit()
    logger.info("Refreshed token for account %s (expires %s)", account.nick_name, expires_at)
    return new_access


def get_valid_token_sync(db: Session, account_id: int) -> str:
    """同步版 get_valid_token（用于 Celery 等非异步上下文）"""
    import requests as _requests

    account = db.query(WeChatOAuthAccount).filter(
        WeChatOAuthAccount.id == account_id,
        WeChatOAuthAccount.is_active == True,
    ).first()
    if not account:
        raise ValueError(f"OAuth account {account_id} not found")

    now = datetime.now(timezone.utc)
    if account.token_expires_at and account.token_expires_at > now:
        return account.authorizer_access_token

    if not account.authorizer_refresh_token:
        raise ValueError(f"OAuth account {account_id} has no refresh_token")

    # 获取 component_access_token（同步）
    comp_token = _get_component_token_sync()
    if not comp_token:
        raise RuntimeError("Failed to get component_access_token for token refresh")

    # 刷新 authorizer_token
    refresh_url = f"{_BASE}/cgi-bin/component/api_authorizer_token"
    resp = _requests.post(
        refresh_url,
        params={"component_access_token": comp_token},
        json={
            "component_appid": settings.wechat_component_app_id,
            "authorizer_appid": account.app_id,
            "authorizer_refresh_token": account.authorizer_refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    new_access = data.get("authorizer_access_token", "")
    new_refresh = data.get("authorizer_refresh_token", account.authorizer_refresh_token)
    expires_in = data.get("expires_in", 7200)

    account.authorizer_access_token = new_access
    account.authorizer_refresh_token = new_refresh
    expires_at = datetime.fromtimestamp(now.timestamp() + expires_in, tz=timezone.utc)
    account.token_expires_at = expires_at
    db.commit()
    return new_access


def _get_component_token_sync() -> str:
    """同步获取 component_access_token"""
    import requests as _requests

    global _component_access_token, _component_access_token_expires

    if _component_access_token and time.time() < _component_access_token_expires:
        return _component_access_token

    ticket = _get_verify_ticket()
    if not ticket:
        raise RuntimeError("component_verify_ticket not available")

    resp = _requests.post(
        f"{_BASE}/cgi-bin/component/api_component_token",
        json={
            "component_appid": settings.wechat_component_app_id,
            "component_appsecret": settings.wechat_component_app_secret,
            "component_verify_ticket": ticket,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    _component_access_token = data.get("component_access_token", "")
    _component_access_token_expires = time.time() + data.get("expires_in", 600) - 120
    return _component_access_token


# ---------------------------------------------------------------------------
# 微信回调验证（用于 component_verify_ticket 推送）
# ---------------------------------------------------------------------------


def verify_callback_signature(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> Optional[str]:
    """验证微信回调 URL 的签名

    首次验证时返回 echostr 表示验证通过
    """
    token = settings.wechat_component_token
    if not token:
        return None

    tmp_list = sorted([token, timestamp, nonce])
    tmp_str = "".join(tmp_list)
    digest = hashlib.sha1(tmp_str.encode("utf-8")).hexdigest()

    if digest == msg_signature:
        return echostr
    return None


def parse_callback_xml(xml_body: bytes) -> dict:
    """解析微信推送的 XML 回调

    解析 component_verify_ticket 或授权结果通知
    """
    root = ET.fromstring(xml_body)
    result = {}
    for child in root:
        result[child.tag] = child.text or ""
    return result


def handle_component_verify_ticket(xml_body: bytes) -> str:
    """处理 component_verify_ticket 推送

    Returns:
        提取到的 ticket
    """
    data = parse_callback_xml(xml_body)
    ticket = data.get("ComponentVerifyTicket", "")
    if ticket:
        set_verify_ticket(ticket)
        logger.info("Received component_verify_ticket: %s...", ticket[:20])
    return ticket
