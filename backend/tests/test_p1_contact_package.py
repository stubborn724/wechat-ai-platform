"""P1.1 联系资料包 CRUD 测试"""

import pytest
from app.models.mysql_models import ContactPackage
from app.services.wechat_contact_package_service import (
    create_package, update_package, get_package, list_packages,
    enable_package, disable_package, soft_delete_package,
)

_uid = 0


def _name(prefix="pkg"):
    global _uid
    _uid += 1
    return f"{prefix}_{_uid}"


class TestContactPackageCRUD:
    def test_create_package(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": _name(), "contact_name": "Xiao Lin",
            "wechat_id": "wx001", "phone": "13800138000",
        }, test_user.id)
        assert pkg.id > 0
        assert pkg.is_enabled is False
        assert pkg.deleted_at is None

    def test_get_package(self, db, test_tenant, test_account, test_user):
        nm = _name()
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": nm}, test_user.id)
        data = get_package(db, test_tenant.id, pkg.id)
        assert data is not None
        assert data["name"] == nm

    def test_update_package(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        updated = update_package(db, test_tenant.id, pkg.id, {"name": "newname"})
        assert updated.name == "newname"

    def test_name_unique_per_account(self, db, test_tenant, test_account, test_user):
        nm = _name()
        create_package(db, test_tenant.id, test_account.id, {"name": nm}, test_user.id)
        with pytest.raises(Exception):
            create_package(db, test_tenant.id, test_account.id, {"name": nm}, test_user.id)

    def test_only_one_default(self, db, test_tenant, test_account, test_user):
        nm1, nm2 = _name("d1"), _name("d2")
        p1 = create_package(db, test_tenant.id, test_account.id, {"name": nm1, "is_default": True}, test_user.id)
        p2 = create_package(db, test_tenant.id, test_account.id, {"name": nm2, "is_default": True}, test_user.id)
        after1 = get_package(db, test_tenant.id, p1.id)
        assert after1["is_default"] is False
        after2 = get_package(db, test_tenant.id, p2.id)
        assert after2["is_default"] is True

    def test_tenant_isolation(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        data = get_package(db, 99999, pkg.id)
        assert data is None

    def test_enable_without_qr_raises(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        with pytest.raises(ValueError, match="missing qr"):
            enable_package(db, test_tenant.id, pkg.id)

    def test_disable_package(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        disabled = disable_package(db, test_tenant.id, pkg.id)
        assert disabled.is_enabled is False

    def test_soft_delete(self, db, test_tenant, test_account, test_user):
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        ok = soft_delete_package(db, test_tenant.id, pkg.id)
        assert ok is True
        data = get_package(db, test_tenant.id, pkg.id)
        assert data is None

    def test_list_packages(self, db, test_tenant, test_account, test_user):
        create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        create_package(db, test_tenant.id, test_account.id, {"name": _name()}, test_user.id)
        items, total = list_packages(db, test_tenant.id, page=1, page_size=10)
        assert total >= 2
        assert len(items) >= 2
