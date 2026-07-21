"""FastAPI 依赖注入"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_mysql_db, get_pg_db
from app.models.mysql_models import User

# 统一使用 /api/v1/auth/login 作为 OAuth2 登录路径
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


@dataclass
class CurrentPrincipal:
    user_id: int
    email: str
    display_name: str
    role: str = "user"
    tenant_id: int = 0


async def get_current_principal(
    token: Optional[str] = Depends(oauth2_scheme),
    token_query: Optional[str] = Query(None, alias="token"),
    db: Session = Depends(get_mysql_db),
) -> Optional[CurrentPrincipal]:
    """从 JWT token 解析当前用户。优先取 Authorization header，
    若没有（如 SSE EventSource 无法设置自定义 header）则回退到查询参数 ?token=。"""
    jwt_token = token or token_query
    if not jwt_token:
        return None
    from app.services.auth_service import decode_token
    payload = decode_token(jwt_token)
    if payload is None:
        return None
    user_id = int(payload.get("sub", 0))
    tenant_id = int(payload.get("tenant_id", 0))
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        return None
    return CurrentPrincipal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=tenant_id,
    )


def require_auth(principal: Optional[CurrentPrincipal] = Depends(get_current_principal)):
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal
