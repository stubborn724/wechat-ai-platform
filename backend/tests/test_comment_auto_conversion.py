"""评论线索自动转化闭环测试。

这些测试覆盖“评论后自动化为主”的核心业务承诺：系统拿到评论线索后，
应优先尝试自动发送联系方式资料包；如果微信客服消息不可达，则不能把
发送任务误判为成功，后续才能可靠进入引导或人工兜底流程。
"""

import uuid
from types import SimpleNamespace

import pytest

from app.database import MysqlSessionLocal
from app.models.mysql_models import Tenant, User, WeChatAccount, WeChatComment, WeChatMessage
from app.services.wechat_contact_package_service import create_package
from app.services.wechat_delivery_service import create_delivery, execute_delivery, get_delivery
from app.services.wechat_eligibility_service import EligibilityResult


def _eligible_result():
    """构造可私信资格结果，避免测试依赖真实微信接口。"""
    from datetime import datetime, timezone

    return EligibilityResult(
        status="eligible",
        reason_code="TEST_ELIGIBLE",
        reason_text="测试用户可发送",
        recommended_action="SEND_CONTACT",
        checked_at=datetime.now(timezone.utc),
        source="test",
    )


def _create_enabled_package(db, tenant_id, account_id, user_id):
    """创建带二维码的启用资料包，模拟真实运营配置。"""
    pkg = create_package(
        db,
        tenant_id,
        account_id,
        {
            "name": f"auto_pkg_{uuid.uuid4().hex[:8]}",
            "contact_name": "客服小林",
            "wechat_id": "wx_service_001",
            "phone": "13800138000",
            "text_content": "您好，这是我们的联系方式，请添加二维码咨询。",
            "qr_asset_id": 999,
            "is_default": True,
        },
        user_id,
    )
    pkg.is_enabled = True
    db.commit()
    return pkg


def _create_primitives(db):
    """创建测试所需的最小租户、用户和公众号，避免依赖种子数据。"""
    tenant = Tenant(name=f"tenant_{uuid.uuid4().hex[:6]}", slug=f"t{uuid.uuid4().hex[:10]}")
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        display_name="测试运营",
    )
    account = WeChatAccount(
        tenant_id=1,
        name=f"公众号_{uuid.uuid4().hex[:6]}",
        app_id=f"wx{uuid.uuid4().hex[:16]}",
        auth_mode="credential",
        status="active",
    )
    db.add(tenant)
    db.flush()
    db.add(user)
    db.flush()
    account.tenant_id = tenant.id
    db.add(account)
    db.commit()
    db.refresh(tenant)
    db.refresh(user)
    db.refresh(account)
    return tenant, user, account


@pytest.mark.asyncio
async def test_auto_conversion_sends_default_contact_package(
    db,
    monkeypatch,
):
    """可私信用户的新评论线索应自动发送默认资料包。"""
    from app.services.comment_auto_conversion_service import process_comment_lead_auto_conversion
    import app.services.comment_auto_conversion_service as auto_service
    import app.services.wechat_delivery_service as delivery_service

    test_tenant, test_user, test_account = _create_primitives(db)
    pkg = _create_enabled_package(db, test_tenant.id, test_account.id, test_user.id)

    comment = WeChatComment(
        tenant_id=test_tenant.id,
        account_id=test_account.id,
        msg_id="auto_msg_data_id",
        comment_id=f"{uuid.uuid4().int % 100000000}",
        openid=f"openid_{uuid.uuid4().hex[:8]}",
        nickname="测试用户",
        content="想了解一下联系方式",
        status="pending",
    )
    db.add(comment)
    db.commit()

    from app.services.wechat_lead_service import create_leads_from_comments

    create_leads_from_comments(db, test_tenant.id, test_account.id, [comment.id])
    lead = db.query(auto_service.CommentLead).filter(
        auto_service.CommentLead.comment_id == comment.id,
    ).first()

    async def fake_check(*args, **kwargs):
        return _eligible_result()

    async def fake_prepare_media(*args, **kwargs):
        return SimpleNamespace(
            media_id="mock_qr_media_id",
            is_mock=True,
            last_error_code=None,
            last_error_message=None,
        )

    import app.services.wechat_eligibility_service as elig_service

    monkeypatch.setattr(elig_service, "check_contact_eligibility", fake_check)
    monkeypatch.setattr(delivery_service._elig, "check_contact_eligibility", fake_check)
    monkeypatch.setattr(delivery_service._media, "get_or_prepare_image_media", fake_prepare_media)

    result = await process_comment_lead_auto_conversion(db, test_tenant.id, lead.id, test_user.id)

    verify_db = MysqlSessionLocal()
    try:
        delivery = get_delivery(verify_db, test_tenant.id, result["delivery_id"])
        sent_messages = verify_db.query(WeChatMessage).filter(
            WeChatMessage.tenant_id == test_tenant.id,
            WeChatMessage.account_id == test_account.id,
            WeChatMessage.openid == comment.openid,
            WeChatMessage.status == "sent",
        ).all()
    finally:
        verify_db.close()

    assert result["action"] == "sent_contact"
    assert delivery["status"] == "success"
    assert delivery["text_status"] == "success"
    assert delivery["qr_status"] == "success"
    assert {m.msg_type for m in sent_messages} == {"text", "image"}


@pytest.mark.asyncio
async def test_delivery_treats_wechat_send_error_as_failed(
    db,
    monkeypatch,
):
    """微信客服消息返回错误码时，发送任务不能被误标为成功。"""
    import app.services.wechat_delivery_service as delivery_service

    test_tenant, test_user, test_account = _create_primitives(db)
    pkg = _create_enabled_package(db, test_tenant.id, test_account.id, test_user.id)

    delivery = create_delivery(
        db,
        test_tenant.id,
        None,
        test_account.id,
        f"openid_{uuid.uuid4().hex[:8]}",
        pkg.id,
        test_user.id,
        uuid.uuid4().hex,
    )

    async def fake_check(*args, **kwargs):
        return _eligible_result()

    async def fake_prepare_media(*args, **kwargs):
        return SimpleNamespace(
            media_id="mock_qr_media_id",
            is_mock=True,
            last_error_code=None,
            last_error_message=None,
        )

    async def fake_send_text(*args, **kwargs):
        return {"errcode": 45015, "errmsg": "response out of time limit or subscription is canceled"}

    async def fake_send_image(*args, **kwargs):
        raise AssertionError("文本发送失败后不应继续发送二维码")

    monkeypatch.setattr(delivery_service._elig, "check_contact_eligibility", fake_check)
    monkeypatch.setattr(delivery_service._media, "get_or_prepare_image_media", fake_prepare_media)
    monkeypatch.setattr(delivery_service._wms, "send_text_message", fake_send_text)
    monkeypatch.setattr(delivery_service._wms, "send_image_message", fake_send_image)

    await execute_delivery(delivery.id)

    verify_db = MysqlSessionLocal()
    try:
        data = get_delivery(verify_db, test_tenant.id, delivery.id)
    finally:
        verify_db.close()
    assert data["status"] == "failed"
    assert data["text_status"] == "failed"
    assert data["qr_status"] == "failed"
    assert "45015" in data["text_error_message"]
