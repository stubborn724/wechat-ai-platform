"""WeChat accounts CRUD"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import WeChatAccount, AccountCredential, Membership

router = APIRouter()


# --- Schemas ---

class AccountCreate(BaseModel):
    name: str
    app_id: str
    app_secret: Optional[str] = None
    auth_mode: str = "token"
    capabilities: Optional[dict] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    app_id: Optional[str] = None
    auth_mode: Optional[str] = None
    status: Optional[str] = None
    capabilities: Optional[dict] = None


class AccountResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    app_id: str
    auth_mode: str
    status: str
    capabilities: Optional[dict] = None
    last_health_at: Optional[datetime] = None
    last_health_error: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AccountListResponse(BaseModel):
    total: int
    items: List[AccountResponse]


# --- Routes ---

@router.get("/accounts", response_model=AccountListResponse)
def list_accounts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List WeChat accounts for the current tenant."""
    query = db.query(WeChatAccount).filter(WeChatAccount.deleted_at.is_(None))

    if status:
        query = query.filter(WeChatAccount.status == status)

    total = query.count()
    items = query.order_by(WeChatAccount.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return AccountListResponse(total=total, items=items)


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(
    account_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get WeChat account detail."""
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


@router.post("/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(
    req: AccountCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new WeChat account."""
    existing = db.query(WeChatAccount).filter(
        WeChatAccount.app_id == req.app_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account with this app_id already exists")

    # resolve tenant_id from the current user's active membership
    membership = db.query(Membership).filter(
        Membership.user_id == principal.user_id,
        Membership.is_active.is_(True),
    ).first()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active tenant membership")

    account = WeChatAccount(
        tenant_id=membership.tenant_id,
        name=req.name,
        app_id=req.app_id,
        auth_mode=req.auth_mode,
        capabilities=req.capabilities,
    )
    db.add(account)
    db.flush()

    if req.app_secret:
        credential = AccountCredential(
            tenant_id=membership.tenant_id,
            account_id=account.id,
            encrypted_secret=req.app_secret,
            key_version="v1",
        )
        db.add(credential)

    db.commit()
    db.refresh(account)
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    req: AccountUpdate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update a WeChat account."""
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(account, field, value)

    db.commit()
    db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Soft delete a WeChat account."""
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    account.deleted_at = datetime.utcnow()
    db.commit()
