"""联系资料包 CRUD 服务"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import ContactPackage, WeChatAccount

logger = logging.getLogger(__name__)


def list_packages(
    db: Session,
    tenant_id: int,
    account_id: Optional[int] = None,
    enabled_only: bool = False,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """资料包列表"""
    q = db.query(ContactPackage).filter(
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    )
    if account_id:
        q = q.filter(ContactPackage.account_id == account_id)
    if enabled_only:
        q = q.filter(ContactPackage.is_enabled == True)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(ContactPackage.name.ilike(like))

    total = q.count()
    rows = q.order_by(ContactPackage.is_default.desc(), ContactPackage.updated_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    items = []
    for pkg in rows:
        items.append(_pkg_to_dict(db, pkg))
    return items, total


def get_package(db: Session, tenant_id: int, pkg_id: int) -> Optional[dict]:
    """资料包详情"""
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == pkg_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        return None
    return _pkg_to_dict(db, pkg)


def _pkg_to_dict(db: Session, pkg: ContactPackage) -> dict:
    from app.models.mysql_models import Asset, WechatMediaAsset

    qr_url = None
    media_status = None
    if pkg.qr_asset_id:
        asset = db.query(Asset).filter(Asset.id == pkg.qr_asset_id).first()
        if asset:
            qr_url = asset.url or asset.storage_key
        ma = db.query(WechatMediaAsset).filter(
            WechatMediaAsset.tenant_id == pkg.tenant_id,
            WechatMediaAsset.account_id == pkg.account_id,
            WechatMediaAsset.asset_id == pkg.qr_asset_id,
        ).first()
        if ma:
            media_status = ma.status

    account = db.query(WeChatAccount).filter(WeChatAccount.id == pkg.account_id).first()
    return {
        "id": pkg.id,
        "account_id": pkg.account_id,
        "account_name": account.name if account else "",
        "name": pkg.name,
        "description": pkg.description,
        "contact_name": pkg.contact_name,
        "wechat_id": pkg.wechat_id,
        "phone": pkg.phone,
        "text_content": pkg.text_content,
        "qr_asset_id": pkg.qr_asset_id,
        "qr_url": qr_url,
        "media_status": media_status,
        "is_default": pkg.is_default,
        "is_enabled": pkg.is_enabled,
        "usage_count": pkg.usage_count,
        "created_by": pkg.created_by,
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


def _check_contact_fields(pkg: ContactPackage) -> bool:
    """至少联系人和微信号/电话填一项"""
    return bool(pkg.contact_name and (pkg.wechat_id or pkg.phone))


def create_package(db: Session, tenant_id: int, account_id: int, data: dict, operator_id: int) -> ContactPackage:
    """创建资料包"""
    pkg = ContactPackage(
        tenant_id=tenant_id,
        account_id=account_id,
        name=data["name"],
        description=data.get("description"),
        contact_name=data.get("contact_name"),
        wechat_id=data.get("wechat_id"),
        phone=data.get("phone"),
        text_content=data.get("text_content"),
        qr_asset_id=data.get("qr_asset_id"),
        is_default=data.get("is_default", False),
        is_enabled=data.get("is_enabled", False),
        created_by=operator_id,
    )

    # 设为默认时清除原默认
    if pkg.is_default:
        _clear_other_defaults(db, tenant_id, account_id, None)

    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    return pkg


def update_package(db: Session, tenant_id: int, pkg_id: int, data: dict) -> Optional[ContactPackage]:
    """更新资料包"""
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == pkg_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        return None

    if "name" in data:
        pkg.name = data["name"]
    if "description" in data:
        pkg.description = data.get("description")
    if "contact_name" in data:
        pkg.contact_name = data.get("contact_name")
    if "wechat_id" in data:
        pkg.wechat_id = data.get("wechat_id")
    if "phone" in data:
        pkg.phone = data.get("phone")
    if "text_content" in data:
        pkg.text_content = data.get("text_content")
    if "qr_asset_id" in data:
        pkg.qr_asset_id = data.get("qr_asset_id")

    # 切换默认
    if "is_default" in data and data["is_default"] and not pkg.is_default:
        _clear_other_defaults(db, tenant_id, pkg.account_id, pkg_id)
        pkg.is_default = True
    elif "is_default" in data and not data["is_default"]:
        pkg.is_default = False

    if "is_enabled" in data:
        pkg.is_enabled = data["is_enabled"]

    db.commit()
    db.refresh(pkg)
    return pkg


def _clear_other_defaults(db: Session, tenant_id: int, account_id: int, exclude_id: Optional[int]):
    """清除其他默认资料包"""
    q = db.query(ContactPackage).filter(
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.account_id == account_id,
        ContactPackage.is_default == True,
        ContactPackage.deleted_at.is_(None),
    )
    if exclude_id:
        q = q.filter(ContactPackage.id != exclude_id)
    for p in q.all():
        p.is_default = False


def enable_package(db: Session, tenant_id: int, pkg_id: int) -> Optional[ContactPackage]:
    """启用资料包（需二维码配置完整）"""
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == pkg_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        return None
    if not pkg.qr_asset_id:
        raise ValueError("missing qr_asset_id: cannot enable package without qr code")
    if pkg.is_enabled:
        return pkg
    pkg.is_enabled = True
    db.commit()
    db.refresh(pkg)
    return pkg


def disable_package(db: Session, tenant_id: int, pkg_id: int) -> Optional[ContactPackage]:
    """停用资料包"""
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == pkg_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        return None
    pkg.is_enabled = False
    db.commit()
    db.refresh(pkg)
    return pkg


def soft_delete_package(db: Session, tenant_id: int, pkg_id: int) -> bool:
    """软删除资料包（被引用的拒绝）"""
    from app.models.mysql_models import ContactDelivery
    pkg = db.query(ContactPackage).filter(
        ContactPackage.id == pkg_id,
        ContactPackage.tenant_id == tenant_id,
        ContactPackage.deleted_at.is_(None),
    ).first()
    if not pkg:
        return False

    # 检查是否被 delivery 引用
    ref = db.query(ContactDelivery).filter(
        ContactDelivery.package_id == pkg_id,
    ).first()
    if ref:
        raise ValueError("资料包已被发送任务引用，无法删除，请先停用")

    pkg.deleted_at = datetime.now(timezone.utc)
    pkg.is_enabled = False
    db.commit()
    return True
