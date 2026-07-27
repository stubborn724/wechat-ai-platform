"""用户认证"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_mysql_db
from app.models.mysql_models import Membership, User
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
)
from app.deps import get_current_principal, CurrentPrincipal

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


def _set_auth_cookie(response: Response, access_token: str, refresh_token: str = ""):
    """设置 HttpOnly Secure SameSite Cookie（XSS 防护）

    access_token 短期有效，refresh_token 仅用于刷新接口。
    """
    access_max_age = settings.jwt_access_token_expire_minutes * 60
    refresh_max_age = settings.jwt_refresh_token_expire_days * 86400

    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=access_max_age,
        httponly=True,
        samesite="strict",
        # 生产环境开启 Secure
        secure=settings.environment == "production",
        path="/api/v1",
    )
    if refresh_token:
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            max_age=refresh_max_age,
            httponly=True,
            samesite="strict",
            secure=settings.environment == "production",
            path="/api/v1/auth",
        )


def _clear_auth_cookie(response: Response):
    """清除认证 Cookie"""
    response.delete_cookie("access_token", path="/api/v1")
    response.delete_cookie("refresh_token", path="/api/v1/auth")


@router.post("/auth/login")
async def login(
    req: LoginRequest,
    response: Response,
    db: Session = Depends(get_mysql_db),
):
    """用户登录"""
    user = authenticate_user(db, req.email, req.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")

    membership = db.query(Membership).filter(
        Membership.user_id == user.id,
        Membership.is_active.is_(True),
    ).first()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无可用租户")

    access_token = create_access_token(user.id, membership.tenant_id)
    refresh_token_str = create_refresh_token(user.id, membership.tenant_id)

    _set_auth_cookie(response, access_token, refresh_token_str)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }


@router.post("/auth/register")
async def register(
    req: RegisterRequest,
    response: Response,
    db: Session = Depends(get_mysql_db),
):
    """用户注册"""
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已注册")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        display_name=req.display_name,
    )
    db.add(user)
    db.flush()

    # 创建默认租户
    from app.models.mysql_models import Tenant
    tenant_slug = req.email.split("@")[0]
    tenant = Tenant(name=f"{req.display_name}的团队", slug=tenant_slug)
    db.add(tenant)
    db.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role="admin")
    db.add(membership)
    db.commit()

    access_token = create_access_token(user.id, tenant.id)
    refresh_token_str = create_refresh_token(user.id, tenant.id)

    _set_auth_cookie(response, access_token, refresh_token_str)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }


@router.post("/auth/refresh")
async def refresh_token_endpoint(
    response: Response,
    refresh_token: str = None,
    request: Request = None,
    db: Session = Depends(get_mysql_db),
):
    """刷新 token（支持 body 参数和 Cookie 两种方式）"""
    token_value = refresh_token
    if not token_value and request:
        token_value = request.cookies.get("refresh_token")
    if not token_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 refresh token")

    payload = decode_token(token_value)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 refresh token")

    user_id = int(payload["sub"])
    tenant_id = payload.get("tenant_id")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    new_access = create_access_token(user.id, tenant_id)
    new_refresh = create_refresh_token(user.id, tenant_id)

    _set_auth_cookie(response, new_access, new_refresh)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@router.get("/auth/me")
async def get_current_user(principal: CurrentPrincipal = Depends(get_current_principal)):
    """获取当前用户信息"""
    return principal


@router.post("/auth/logout")
async def logout(response: Response):
    """退出登录（清除 HttpOnly Cookie）"""
    _clear_auth_cookie(response)
    return {"message": "ok"}
