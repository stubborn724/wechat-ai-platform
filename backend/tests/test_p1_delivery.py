"""P1.4+P1.5 资料发送任务 + 重试测试"""

import uuid
import pytest
from app.services.wechat_delivery_service import (
    create_delivery, get_delivery, list_deliveries_by_lead,
)

from app.models.mysql_models import ContactDelivery


@pytest.mark.asyncio
class TestDelivery:
    async def test_create_delivery_rejects_disabled_package(self, db, test_tenant, test_account, test_user):
        """已停用的资料包应拒绝"""
        from app.services.wechat_contact_package_service import create_package
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": "del_test_disabled"}, test_user.id)
        # 不启用
        with pytest.raises(ValueError, match="disabled"):
            create_delivery(db, test_tenant.id, None, test_account.id, "test_openid",
                            pkg.id, test_user.id, uuid.uuid4().hex)

    async def test_create_delivery_rejects_no_qr(self, db, test_tenant, test_account, test_user):
        """无二维码的资料包应拒绝"""
        from app.services.wechat_contact_package_service import create_package, enable_package
        pkg = create_package(db, test_tenant.id, test_account.id, {"name": "del_test_noqr"}, test_user.id)
        with pytest.raises(ValueError, match="missing qr"):
            enable_package(db, test_tenant.id, pkg.id)

    async def test_create_delivery_idempotency(self, db, test_tenant, test_account, test_user):
        """相同幂等键返回相同 delivery"""
        key = uuid.uuid4().hex
        from app.services.wechat_contact_package_service import create_package, enable_package
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": "del_test_idemp", "qr_asset_id": 1,
        }, test_user.id)
        pkg.qr_asset_id = 999  # fake asset id just for testing
        pkg.is_enabled = True
        db.commit()

        d1 = create_delivery(db, test_tenant.id, None, test_account.id, "idemp_openid",
                             pkg.id, test_user.id, key)
        d2 = create_delivery(db, test_tenant.id, None, test_account.id, "idemp_openid",
                             pkg.id, test_user.id, key)
        assert d1.id == d2.id  # 幂等

    async def test_get_delivery(self, db, test_tenant, test_account, test_user):
        from app.services.wechat_contact_package_service import create_package, enable_package
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": "查询发送测试", "qr_asset_id": 1,
        }, test_user.id)
        pkg.qr_asset_id = 999
        pkg.is_enabled = True
        db.commit()

        d = create_delivery(db, test_tenant.id, None, test_account.id, "query_openid",
                            pkg.id, test_user.id, uuid.uuid4().hex)
        data = get_delivery(db, test_tenant.id, d.id)
        assert data is not None
        assert data["status"] == "pending"
        assert data["delivery_mode"] in ("live", "mock")

    async def test_cross_tenant_denied(self, db, test_tenant, test_account, test_user):
        """跨租户不可访问"""
        from app.services.wechat_contact_package_service import create_package, enable_package
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": "跨租户测试", "qr_asset_id": 1,
        }, test_user.id)
        pkg.qr_asset_id = 999
        pkg.is_enabled = True
        db.commit()

        d = create_delivery(db, test_tenant.id, None, test_account.id, "cross_openid",
                            pkg.id, test_user.id, uuid.uuid4().hex)
        data = get_delivery(db, 99999, d.id)
        assert data is None

    async def test_delivery_saves_package_snapshot(self, db, test_tenant, test_account, test_user):
        """delivery 保存资料包快照"""
        from app.services.wechat_contact_package_service import create_package, enable_package
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": "快照测试", "qr_asset_id": 1,
            "contact_name": "小张", "wechat_id": "testwx002",
        }, test_user.id)
        pkg.qr_asset_id = 999
        pkg.is_enabled = True
        db.commit()

        d = create_delivery(db, test_tenant.id, None, test_account.id, "snap_openid",
                            pkg.id, test_user.id, uuid.uuid4().hex)
        data = get_delivery(db, test_tenant.id, d.id)
        assert data["package_snapshot"] is not None
        assert data["package_snapshot"]["name"] == "快照测试"
        assert data["package_snapshot"]["contact_name"] == "小张"
