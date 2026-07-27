"""微信回调服务 — 验签、解析、去重、入库"""

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import ConversationMessage, WeChatAccount, WeChatUserInteraction

logger = logging.getLogger(__name__)


def verify_signature(token: str, timestamp: str, nonce: str, signature: str) -> bool:
    """微信签名校验: SHA1(sorted(token, timestamp, nonce))"""
    tmp = sorted([token, timestamp, nonce])
    s = hashlib.sha1("".join(tmp).encode("utf-8")).hexdigest()
    return s == signature


def parse_xml(xml_body: bytes) -> dict:
    """安全解析微信回调 XML"""
    root = ET.fromstring(xml_body)
    data = {}
    for child in root:
        tag = child.tag
        text = child.text or ""
        # 处理 CDATA
        data[tag] = text.strip()
    return data


def normalize_keyword(keyword: str) -> str:
    """关键词标准化

    - 去除首尾空格
    - 全角英数字→半角
    - 全角空格→半角
    - 中文标点统一
    - 英文统一小写
    """
    if not keyword:
        return ""
    k = keyword.strip()
    # 全角 -> 半角
    result = []
    for ch in k:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    k = "".join(result)
    # 英文统一小写
    k = k.lower()
    return k


def compute_event_fingerprint(account_id: int, openid: str, event_type: str,
                               event_key: str, create_time: str) -> str:
    """生成无 MsgId 事件的去重指纹"""
    raw = f"{account_id}|{openid}|{event_type}|{event_key}|{create_time}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def find_account_by_key(db: Session, callback_key: str) -> Optional[WeChatAccount]:
    """通过 callback_key 查找公众号"""
    return db.query(WeChatAccount).filter(
        WeChatAccount.callback_key == callback_key,
        WeChatAccount.deleted_at.is_(None),
    ).first()


def handle_callback_message(
    db: Session,
    account: WeChatAccount,
    xml_data: dict,
) -> dict:
    """处理回调消息：验签已在入口完成，此处做解析、去重、入库

    返回: {"handled": bool, "message_id": int | None, "duplicate": bool}
    """
    msg_type = xml_data.get("MsgType", "text")
    event = xml_data.get("Event", "")
    event_key = xml_data.get("EventKey", "")
    content = xml_data.get("Content", "")
    openid = xml_data.get("FromUserName", "")
    create_time_str = xml_data.get("CreateTime", "0")
    msg_id = xml_data.get("MsgId", "")

    # 标准化消息类型
    message_type = msg_type.lower()
    if message_type == "event":
        message_type = "event"

    # 解析 create_time
    create_time_dt = None
    try:
        ts = int(create_time_str)
        create_time_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError):
        pass

    # 1. MsgId 去重
    msg_id_str = str(msg_id).strip() if msg_id else ""

    # 2. event_fingerprint 去重（无 MsgId 事件）
    fingerprint = ""
    if not msg_id_str and message_type == "event":
        fingerprint = compute_event_fingerprint(
            account.id, openid, event, event_key, create_time_str,
        )

    # 3. 幂等检查
    if msg_id_str:
        existing = db.query(ConversationMessage).filter(
            ConversationMessage.account_id == account.id,
            ConversationMessage.msg_id == msg_id_str,
        ).first()
        if existing:
            logger.info("Duplicate msg_id=%s, skip", msg_id_str[:16])
            return {"handled": True, "message_id": existing.id, "duplicate": True}

    if fingerprint:
        existing = db.query(ConversationMessage).filter(
            ConversationMessage.account_id == account.id,
            ConversationMessage.event_fingerprint == fingerprint,
        ).first()
        if existing:
            logger.info("Duplicate event fingerprint=%s, skip", fingerprint[:16])
            return {"handled": True, "message_id": existing.id, "duplicate": True}

    # 4. 入库
    msg = ConversationMessage(
        tenant_id=account.tenant_id,
        account_id=account.id,
        openid=openid,
        direction="inbound",
        message_type=message_type,
        content=content if message_type == "text" else (xml_data.get("Recognition", "") or ""),
        msg_id=msg_id_str or None,
        event_fingerprint=fingerprint or None,
        event_type=event if message_type == "event" else None,
        event_key=event_key if message_type == "event" else None,
        create_time=create_time_dt,
        processing_status="received",
        raw_xml=xml_data,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # 5. 快速返回
    return {"handled": True, "message_id": msg.id, "duplicate": False, "msg_type": message_type}
