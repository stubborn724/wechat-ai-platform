"""用户认证"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.models.mysql_models import Membership, RefreshToken, User
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


@router.post("/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_mysql_db)):
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
    refresh_token = create_refresh_token(user.id, membership.tenant_id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
    }


@router.post("/auth/register")
async def register(req: RegisterRequest, db: Session = Depends(get_mysql_db)):
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
    tenant_slug = req.email.split("@")[0]
    from app.models.mysql_models import Tenant
    tenant = Tenant(name=f"{req.display_name}的团队", slug=tenant_slug)
    db.add(tenant)
    db.flush()

    membership = Membership(tenant_id=tenant.id, user_id=user.id, role="admin")
    db.add(membership)
    db.commit()

    access_token = create_access_token(user.id, tenant.id)
    refresh_token_str = create_refresh_token(user.id, tenant.id)

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
async def refresh_token_endpoint(refresh_token: str, db: Session = Depends(get_mysql_db)):
    """刷新 token"""
    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 refresh token")

    user_id = int(payload["sub"])
    tenant_id = payload.get("tenant_id")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    new_access = create_access_token(user.id, tenant_id)
    new_refresh = create_refresh_token(user.id, tenant_id)
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
async def logout():
    """退出登录"""
    return {"message": "ok"}
