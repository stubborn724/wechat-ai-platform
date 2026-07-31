"""三态私信资格检查服务"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 微信错误码 → 资格原因码映射
WECHAT_ERROR_MAP = {
    # IP 白名单 / 权限
    48001: ("ACCOUNT_PERMISSION_MISSING", "API 权限不足，请确认 IP 白名单已配置"),
    48002: ("ACCOUNT_PERMISSION_MISSING", "API 权限不足"),
    40001: ("INVALID_CREDENTIAL", "access_token 无效或过期"),
    40014: ("INVALID_CREDENTIAL", "access_token 无效"),
    42001: ("TOKEN_EXPIRED", "access_token 已过期"),
    # 用户相关
    43004: ("USER_NOT_FOLLOWING", "用户未关注公众号"),
    45015: ("NO_ACTIVE_SESSION", "用户无有效客服会话（超过48小时未互动）"),
    45047: ("MESSAGE_LIMIT_REACHED", "客服消息发送频率超限"),
    48004: ("USER_BLOCKED", "用户已拒收消息"),
    # 网络 / 超时
    -1: ("WECHAT_API_UNAVAILABLE", "微信 API 系统繁忙"),
    -2: ("NETWORK_TIMEOUT", "请求微信 API 超时"),
    # 通用
    40003: ("INVALID_OPENID", "openid 无效"),
    41001: ("TOKEN_MISSING", "缺少 access_token"),
    41002: ("TOKEN_MISSING", "缺少 app_id"),
    41004: ("CREDENTIAL_MISSING", "缺少 app_secret"),
}


def wechat_error_to_eligibility(errcode: int, fallback_msg: str = "") -> tuple:
    """将微信错误码映射为资格原因码和文字"""
    errcode = errcode if isinstance(errcode, int) else -2
    if errcode in WECHAT_ERROR_MAP:
        return WECHAT_ERROR_MAP[errcode]
    return ("WECHAT_API_ERROR", fallback_msg or f"微信 API 返回错误 (errcode={errcode})")


@dataclass
class EligibilityResult:
    status: Literal["eligible", "ineligible", "unknown"]
    reason_code: str
    reason_text: str
    recommended_action: str
    checked_at: datetime
    expires_at: Optional[datetime] = None
    source: str = "fallback"
    wechat_error_code: Optional[int] = None
    is_mock: bool = False

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "reason_code": self.reason_code,
            "reason_text": self.reason_text,
            "recommended_action": self.recommended_action,
            "checked_at": self.checked_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source": self.source,
            "wechat_error_code": self.wechat_error_code,
            "is_mock": self.is_mock,
        }


async def check_contact_eligibility(
    db: Session,
    tenant_id: int,
    account_id: int,
    openid: str,
    force_refresh: bool = False,
) -> EligibilityResult:
    """三态资格检查

    判断顺序:
    1. openid 是否存在
    2. 账号配置及权限
    3. 用户关注状态（通过微信 API）
    4. 已有互动记录 / 会话窗口
    5. 无足够信息 → unknown
    """
    from app.models.mysql_models import CommentLead, WeChatAccount, WeChatMessage

    now = datetime.now(timezone.utc)

    # 1. openid 检查
    if not openid:
        return EligibilityResult(
            status="ineligible",
            reason_code="NO_OPENID",
            reason_text="缺少用户标识",
            recommended_action="NONE",
            checked_at=now,
            source="validation",
        )

    # 2. 账号检查
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        return EligibilityResult(
            status="unknown",
            reason_code="ACCOUNT_NOT_FOUND",
            reason_text="公众号不存在或已删除",
            recommended_action="NOTIFY_ADMIN",
            checked_at=now,
            source="validation",
        )

    # 3. 已有成功发送记录 → 资格可能有效
    recent_msg = db.query(WeChatMessage).filter(
        WeChatMessage.tenant_id == tenant_id,
        WeChatMessage.account_id == account_id,
        WeChatMessage.openid == openid,
        WeChatMessage.status == "sent",
    ).order_by(WeChatMessage.sent_at.desc()).first()

    if recent_msg and recent_msg.sent_at:
        _st = recent_msg.sent_at
        if _st.tzinfo is None:
            _st = _st.replace(tzinfo=timezone.utc)
        window_end = _st + timedelta(hours=48)
        if now < window_end:
            return EligibilityResult(
                status="eligible",
                reason_code="ACTIVE_SESSION",
                reason_text="用户当前处于可发送窗口（基于历史发送记录）",
                recommended_action="SEND_CONTACT",
                checked_at=now,
                expires_at=window_end,
                source="interaction_cache",
            )

    # 4. 尝试微信 API 查询用户信息
    try:
        result = await _check_via_wechat_api(db, account_id, openid)
        if result.status == "eligible":
            return result
        if result.status == "ineligible":
            return result
        # unknown 继续向下
    except Exception as e:
        logger.warning("WeChat API eligibility check failed: %s", e)

    # 5. mock 模式 — 模拟资格（必须标记 is_mock=true，前端需明确展示）
    from app.config import settings as cfg
    if cfg.wechat_send_mode == "mock":
        return EligibilityResult(
            status="eligible",
            reason_code="MOCK_MODE",
            reason_text="模拟模式：默认返回可发送（非真实微信环境）",
            recommended_action="SEND_CONTACT",
            checked_at=now,
            source="mock",
            is_mock=True,
        )

    # 6. 无足够信息
    return EligibilityResult(
        status="unknown",
        reason_code="NO_INTERACTION_RECORD",
        reason_text="无用户互动记录，无法判断私信资格",
        recommended_action="PUBLIC_REPLY_WITH_GUIDE",
        checked_at=now,
        source="fallback",
    )


async def _check_via_wechat_api(db: Session, account_id: int, openid: str) -> EligibilityResult:
    """通过微信 API 查询用户状态"""
    import httpx
    from app.models.mysql_models import AccountCredential, WeChatAccount
    from app.config import settings as cfg
    from app.services.encryption_service import derive_key, decrypt_secret
    from app.services.wechat_gateway_policy import is_wechat_relay_enabled

    now = datetime.now(timezone.utc)
    if is_wechat_relay_enabled():
        return EligibilityResult(
            status="unknown",
            reason_code="RELAY_ENDPOINT_MISSING",
            reason_text="当前启用微信中转站模式，用户资格校验需要中转站提供 user/info 能力",
            recommended_action="PUBLIC_REPLY_ONLY",
            checked_at=now,
            source="relay_policy",
        )

    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        return EligibilityResult(status="unknown", reason_code="ACCOUNT_NOT_FOUND",
                                  reason_text="公众号不存在", recommended_action="NOTIFY_ADMIN",
                                  checked_at=now, source="wechat_api")

    cred = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    ).first()
    if not cred:
        return EligibilityResult(status="unknown", reason_code="CREDENTIAL_MISSING",
                                  reason_text="公众号凭证未配置", recommended_action="NOTIFY_ADMIN",
                                  checked_at=now, source="wechat_api")

    key = derive_key(cfg.credential_key)
    app_secret = decrypt_secret(cred.encrypted_secret, key)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 获取 token
        r = await client.get("https://api.weixin.qq.com/cgi-bin/token", params={
            "grant_type": "client_credential", "appid": account.app_id, "secret": app_secret,
        })
        td = r.json()
        token = td.get("access_token", "")
        if not token:
            return EligibilityResult(status="unknown", reason_code="TOKEN_FAILED",
                                      reason_text=f"获取 access_token 失败: {td.get('errmsg', '')}",
                                      recommended_action="RETRY_LATER",
                                      checked_at=now, source="wechat_api",
                                      wechat_error_code=td.get("errcode"))

        # 查询用户信息
        ur = await client.get("https://api.weixin.qq.com/cgi-bin/user/info",
                              params={"access_token": token, "openid": openid, "lang": "zh_CN"})
        ud = ur.json()

        if ud.get("errcode", 0) != 0:
            code = ud.get("errcode")
            return EligibilityResult(status="unknown", reason_code="USER_INFO_FAILED",
                                      reason_text=f"查询用户信息失败: {ud.get('errmsg', '')}",
                                      recommended_action="RETRY_LATER",
                                      checked_at=now, source="wechat_api",
                                      wechat_error_code=code)

        subscribe = ud.get("subscribe", 0) == 1
        if not subscribe:
            return EligibilityResult(status="ineligible", reason_code="USER_NOT_FOLLOWING",
                                      reason_text="用户未关注公众号",
                                      recommended_action="PUBLIC_REPLY_ONLY",
                                      checked_at=now, source="wechat_api")

        # 用户关注中，尝试发一条客服消息确认窗口
        # 注意：发送测试消息本身可能触发不必要的骚扰
        # 这里只返回 eligible，实际窗口通过发送时检查
        return EligibilityResult(status="eligible", reason_code="USER_FOLLOWING",
                                  reason_text="用户已关注，可尝试发送",
                                  recommended_action="SEND_CONTACT",
                                  checked_at=now, source="wechat_api")


def cache_eligibility(db: Session, lead_id: int, result: EligibilityResult, tenant_id: int):
    """缓存资格结果到 CommentLead"""
    from app.models.mysql_models import CommentLead

    lead = db.query(CommentLead).filter(
        CommentLead.id == lead_id,
        CommentLead.tenant_id == tenant_id,
    ).first()
    if not lead:
        return

    lead.eligibility_status = result.status
    lead.eligibility_reason_code = result.reason_code
    lead.eligibility_reason_text = result.reason_text
    lead.eligibility_recommended_action = result.recommended_action
    lead.eligibility_checked_at = result.checked_at
    lead.eligibility_expires_at = result.expires_at
    lead.eligibility_source = result.source
    lead.eligibility_cache = result.to_dict()
    db.commit()
