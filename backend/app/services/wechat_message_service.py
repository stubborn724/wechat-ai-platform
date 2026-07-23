"""公众号主动私信服务 — 客服消息接口"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import WeChatMessage

logger = logging.getLogger(__name__)

_BASE = "https://api.weixin.qq.com"

MSG_TYPE_LIMITS = {
    "text": "文本消息，最长 2048 字节",
    "image": "图片消息，需 media_id",
    "video": "视频消息，需 media_id",
    "miniprogrampage": "小程序卡片，需 title+pagepath+appid",
}


class WeChatMessageService:
    """微信客服消息 API 封装"""

    def __init__(self, access_token: str):
        self.access_token = access_token

    async def send_text(self, openid: str, text: str) -> dict:
        """发送文本消息"""
        return await self._send(openid, {
            "msgtype": "text",
            "text": {"content": text},
        })

    async def send_image(self, openid: str, media_id: str) -> dict:
        """发送图片消息"""
        return await self._send(openid, {
            "msgtype": "image",
            "image": {"media_id": media_id},
        })

    async def send_video(self, openid: str, media_id: str, title: str = "", description: str = "") -> dict:
        """发送视频消息"""
        return await self._send(openid, {
            "msgtype": "video",
            "video": {
                "media_id": media_id,
                "title": title,
                "description": description,
            },
        })

    async def send_miniprogram_page(
        self,
        openid: str,
        title: str,
        page_path: str,
        app_id: str,
        thumb_media_id: str = "",
    ) -> dict:
        """发送小程序卡片"""
        body = {
            "msgtype": "miniprogrampage",
            "miniprogrampage": {
                "title": title,
                "pagepath": page_path,
                "appid": app_id,
            },
        }
        if thumb_media_id:
            body["miniprogrampage"]["thumb_media_id"] = thumb_media_id
        return await self._send(openid, body)

    async def _send(self, openid: str, body: dict) -> dict:
        url = f"{_BASE}/cgi-bin/message/custom/send?access_token={self.access_token}"
        body["touser"] = openid
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            result = resp.json()
        if result.get("errcode", 0) != 0:
            logger.error("Send message failed: %s", result)
        return result


# ============================================================================
# 高层业务函数
# ============================================================================


async def _get_service(db: Session, account_id: int) -> WeChatMessageService:
    from app.models.mysql_models import AccountCredential, WeChatAccount
    import httpx

    # 用 AppID + AppSecret 获取 access_token
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
    return WeChatMessageService(token)


def _record_message(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    msg_type: str,
    content: Optional[str] = None,
    media_id: Optional[str] = None,
    media_url: Optional[str] = None,
    mini_title: Optional[str] = None,
    mini_page_path: Optional[str] = None,
    mini_app_id: Optional[str] = None,
    status: str = "sent",
    error_message: Optional[str] = None,
) -> WeChatMessage:
    """在数据库记录一条私信"""
    msg = WeChatMessage(
        tenant_id=tenant_id,
        account_id=account_id,
        openid=openid,
        msg_type=msg_type,
        content=content,
        media_id=media_id,
        media_url=media_url,
        mini_title=mini_title,
        mini_page_path=mini_page_path,
        mini_app_id=mini_app_id,
        status=status,
        error_message=error_message,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


async def send_text_message(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    text: str,
) -> dict:
    """发送文本私信"""
    svc = await _get_service(db, account_id)
    result = await svc.send_text(openid, text)

    is_ok = result.get("errcode", -1) == 0
    _record_message(
        db, tenant_id, account_id, openid, "text",
        content=text,
        status="sent" if is_ok else "failed",
        error_message=result.get("errmsg") if not is_ok else None,
    )
    return result


async def send_image_message(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    media_id: str,
    media_url: Optional[str] = None,
) -> dict:
    """发送图片私信"""
    svc = await _get_service(db, account_id)
    result = await svc.send_image(openid, media_id)

    is_ok = result.get("errcode", -1) == 0
    _record_message(
        db, tenant_id, account_id, openid, "image",
        media_id=media_id, media_url=media_url,
        status="sent" if is_ok else "failed",
        error_message=result.get("errmsg") if not is_ok else None,
    )
    return result


async def send_contact_card(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    contact_text: str,
    qr_code_media_id: str,
) -> dict:
    """发送联系方式 + 二维码（文本 + 图片组合消息，需分两次发送）

    微信客服消息不支持富文本混排，所以联系方式文字和二维码图片要分两次发。
    """
    svc = await _get_service(db, account_id)

    # 1. 发联系方式文字
    text_result = await svc.send_text(openid, contact_text)
    _record_message(
        db, tenant_id, account_id, openid, "text",
        content=contact_text,
        status="sent" if text_result.get("errcode", -1) == 0 else "failed",
    )

    # 2. 发二维码图片
    img_result = await svc.send_image(openid, qr_code_media_id)
    _record_message(
        db, tenant_id, account_id, openid, "image",
        media_id=qr_code_media_id,
        status="sent" if img_result.get("errcode", -1) == 0 else "failed",
    )

    return {"text_result": text_result, "image_result": img_result}
