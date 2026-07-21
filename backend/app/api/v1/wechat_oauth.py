"""微信开放平台扫码授权 API 路由"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth, get_current_principal
from app.models.mysql_models import WeChatOAuthAccount
from app.services.wechat_oauth_service import (
    build_authorization_url,
    exchange_auth_code,
    get_pre_auth_code,
    get_valid_token,
    handle_component_verify_ticket,
    save_authorized_account,
    verify_callback_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# 步骤1: 获取授权链接（前端跳转）
# ---------------------------------------------------------------------------


@router.get("/wechat-oauth/auth-url")
async def get_auth_url(principal: CurrentPrincipal = Depends(require_auth)):
    """生成微信扫码授权 URL"""
    try:
        pre_auth_code = await get_pre_auth_code()
        url = build_authorization_url(pre_auth_code)
        return {"auth_url": url, "pre_auth_code": pre_auth_code}
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


# ---------------------------------------------------------------------------
# 步骤2: 微信回调（用户扫码后微信服务器调用）
# ---------------------------------------------------------------------------


@router.get("/wechat-oauth/callback")
async def oauth_callback(
    auth_code: str = Query("", alias="auth_code"),
    expires_in: int = Query(0, alias="expires_in"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(get_current_principal),
):
    """微信扫码授权回调

    Note: 正式环境微信回调不带用户信息，这里简化处理：
    前端收到 auth_code 后，再调用 /wechat-oauth/bind 完成绑定。
    """
    return {"auth_code": auth_code, "expires_in": expires_in}


# ---------------------------------------------------------------------------
# 步骤3: 用 auth_code 绑定授权公众号到当前租户
# ---------------------------------------------------------------------------


@router.post("/wechat-oauth/bind")
async def bind_oauth_account(
    auth_code: str = Query(..., description="授权回调的 auth_code"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """用 auth_code 换取 token 并绑定到当前租户"""
    try:
        auth_data = await exchange_auth_code(auth_code)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"授权失败: {e}")

    account = save_authorized_account(db, principal.tenant_id, auth_data)
    return {
        "id": account.id,
        "app_id": account.app_id,
        "nick_name": account.nick_name,
        "head_img": account.head_img,
        "alias": account.alias,
        "func_info": account.func_info,
    }


# ---------------------------------------------------------------------------
# 列表 & 详情 & 删除
# ---------------------------------------------------------------------------


@router.get("/wechat-oauth/accounts")
def list_oauth_accounts(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取当前租户已授权的公众号列表"""
    accounts = (
        db.query(WeChatOAuthAccount)
        .filter(
            WeChatOAuthAccount.tenant_id == principal.tenant_id,
            WeChatOAuthAccount.is_active == True,
        )
        .order_by(WeChatOAuthAccount.id.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "app_id": a.app_id,
            "nick_name": a.nick_name,
            "head_img": a.head_img,
            "alias": a.alias,
            "service_type_info": a.service_type_info,
            "verify_type_info": a.verify_type_info,
            "user_name": a.user_name,
            "qrcode_url": a.qrcode_url,
            "func_info": a.func_info,
            "token_expires_at": a.token_expires_at.isoformat() if a.token_expires_at else None,
            "created_at": a.created_at.isoformat(),
        }
        for a in accounts
    ]


@router.delete("/wechat-oauth/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def unbind_oauth_account(
    account_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """取消授权"""
    account = (
        db.query(WeChatOAuthAccount)
        .filter(
            WeChatOAuthAccount.id == account_id,
            WeChatOAuthAccount.tenant_id == principal.tenant_id,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    account.is_active = False
    db.commit()


# ---------------------------------------------------------------------------
# 微信服务器推送回调（component_verify_ticket）
# ---------------------------------------------------------------------------


@router.get("/wechat-oauth/ticket")
async def verify_ticket_callback(
    msg_signature: str = Query("", alias="msg_signature"),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
):
    """微信服务器 URL 验证（GET 请求）"""
    result = verify_callback_signature(msg_signature, timestamp, nonce, echostr)
    if result:
        return result
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature verification failed")


@router.post("/wechat-oauth/ticket")
async def receive_ticket_callback(
    request: Request,
):
    """接收微信推送的 component_verify_ticket（POST 请求，XML 格式）"""
    xml_body = await request.body()
    ticket = handle_component_verify_ticket(xml_body)
    if ticket:
        return {"errcode": 0, "errmsg": "ok"}
    return {"errcode": -1, "errmsg": "parse failed"}
