"""P1 mock 验收链路 — 模拟微信 API，验证完整发送流程"""

import uuid
import pytest
from app.config import settings
from app.services.wechat_contact_package_service import create_package, enable_package
from app.services.wechat_delivery_service import create_delivery, get_delivery, execute_delivery
from app.models.mysql_models import ContactDelivery, ContactDeliveryAttempt


@pytest.mark.asyncio
class TestMockAcceptance:
    """两条 mock 验收链路"""

    async def _setup_package(self, db, test_tenant, test_account, test_user):
        """创建并启用一个测试资料包"""
        pkg = create_package(db, test_tenant.id, test_account.id, {
            "name": f"accept_pkg_{uuid.uuid4().hex[:6]}",
            "contact_name": "Xiao Lin",
            "wechat_id": "wx001",
            "phone": "13800138000",
            "text_content": "Welcome to our service! Contact us for more details.",
            "qr_asset_id": 999,
        }, test_user.id)
        pkg.qr_asset_id = 999
        db.commit()
        pkg = enable_package(db, test_tenant.id, pkg.id)
        return pkg

    def _verify_delivery_db_state(self, db, delivery_id, expected_status,
                                   expected_text_status, expected_qr_status):
        """验证数据库中的 delivery 状态（refresh 确保读取最新数据）"""
        db.expire_all()
        d = db.query(ContactDelivery).filter(ContactDelivery.id == delivery_id).first()
        assert d is not None, f"Delivery {delivery_id} not found"
        print(f"\n[DEBUG] delivery {delivery_id}: status={d.status} text={d.text_status} qr={d.qr_status} "
              f"eligibility_snapshot={bool(d.eligibility_snapshot)} package_snapshot={bool(d.package_snapshot)}")
        assert d.status == expected_status, \
            f"Expected status={expected_status}, got {d.status}"
        assert d.text_status == expected_text_status, \
            f"Expected text_status={expected_text_status}, got {d.text_status}"
        assert d.qr_status == expected_qr_status, \
            f"Expected qr_status={expected_qr_status}, got {d.qr_status}"
        return d

    def _verify_attempts(self, db, delivery_id):
        """验证 attempt 记录存在"""
        attempts = db.query(ContactDeliveryAttempt).filter(
            ContactDeliveryAttempt.delivery_id == delivery_id
        ).all()
        assert len(attempts) > 0, "No attempt records found"
        return attempts

    async def test_scenario_1_full_success(self, db, test_tenant, test_account, test_user):
        """链路1: text success + qr success → 全部成功"""
        assert settings.wechat_send_mode == "mock", "必须在 mock 模式下运行"

        pkg = await self._setup_package(db, test_tenant, test_account, test_user)
        key = uuid.uuid4().hex

        # 创建 delivery
        delivery = create_delivery(
            db, test_tenant.id, None, test_account.id,
            "mock_user_openid", pkg.id, test_user.id, key,
        )
        delivery_id = delivery.id
        assert delivery.status == "pending"

        # 执行异步发送
        try:
            await execute_delivery(delivery_id)
        except Exception as exc:
            print(f"\n[DEBUG] execute_delivery raised: {exc}")
            raise

        # 验证 DB 状态
        d = self._verify_delivery_db_state(db, delivery_id, "success", "success", "success")
        assert d.delivery_mode == "mock"
        assert d.package_snapshot is not None
        assert d.eligibility_snapshot is not None

        # 验证 attempt 记录
        attempts = self._verify_attempts(db, delivery_id)
        assert len(attempts) >= 2  # text + qr

        # 验证 API 查询
        data = get_delivery(db, test_tenant.id, delivery_id)
        assert data["status"] == "success"
        assert data["text_status"] == "success"
        assert data["qr_status"] == "success"
        assert data["attempts"] is not None
        assert len(data["attempts"]) >= 2

    async def test_scenario_2_qr_failure_then_retry(self, db, test_tenant, test_account, test_user):
        """链路2: text success + qr failed → 重试 qr → qr success → 全部成功"""

        import builtins as _b

        pkg = await self._setup_package(db, test_tenant, test_account, test_user)
        key = uuid.uuid4().hex

        # 注入 mock_qr_fail 标记到 openid 中，execute_delivery 会识别并模拟 qr 失败
        delivery = create_delivery(
            db, test_tenant.id, None, test_account.id,
            f"mock_qr_fail_{uuid.uuid4().hex[:8]}", pkg.id, test_user.id, key,
        )
        delivery_id = delivery.id
        assert delivery.status == "pending"

        # 模拟第一次发送：将 text 设为成功，qr 设为失败
        # 修改 execute_delivery 的行为：让二维码步骤模拟失败
        # 方法：临时替换 send_image_message 为会抛异常的函数
        from app.services import wechat_message_service as wms
        original_send = wms.send_image_message

        async def mock_qr_fail(*args, **kwargs):
            raise RuntimeError("Mock QR send failure")

        wms.send_image_message = mock_qr_fail

        try:
            await execute_delivery(delivery_id)
        finally:
            wms.send_image_message = original_send

        # 验证: text success + qr failed + 总状态 partial_failed
        d = self._verify_delivery_db_state(db, delivery_id, "partial_failed", "success", "failed")
        assert d.qr_attempts >= 1
        assert d.qr_error_message is not None

        # 重试: 只重试 qr 步骤
        from app.services.wechat_delivery_service import retry_delivery
        retry_key = uuid.uuid4().hex
        # 需要先恢复 send_image_message（已经恢复）

        result = await retry_delivery(
            db, test_tenant.id, delivery_id, "qr", retry_key, test_user.id,
        )

        # 验证重试后: text success + qr success + 总状态 success
        d2 = self._verify_delivery_db_state(db, delivery_id, "success", "success", "success")
        assert d2.qr_attempts >= 2  # 原始1次 + 重试1次

        # 验证 attempt 记录包含重试
        attempts = self._verify_attempts(db, delivery_id)
        qr_attempts_list = [a for a in attempts if a.step == "qr"]
        assert len(qr_attempts_list) >= 2, f"Expected >=2 qr attempts, got {len(qr_attempts_list)}"
        # 至少有1次成功的 qr
        assert any(a.status == "success" for a in qr_attempts_list), "No successful qr attempt"
