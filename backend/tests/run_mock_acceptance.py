"""P1 mock acceptance - standalone script, no chinese chars"""
import asyncio, uuid, sys
sys.path.insert(0, '.')

from app.database import MysqlSessionLocal
from app.models.mysql_models import ContactDelivery
from app.services.wechat_contact_package_service import create_package, enable_package
from app.services.wechat_delivery_service import create_delivery, get_delivery, execute_delivery, retry_delivery
from app.config import settings
from sqlalchemy import text

import time
def _name():
    return f"at_{int(time.time()*1000000)}_{uuid.uuid4().hex[:4]}"


async def scenario_1():
    print("=== SCENARIO 1: full success ===")
    db = MysqlSessionLocal()
    tid = int(db.execute(text("SELECT id FROM tenants LIMIT 1")).scalar())
    aid = int(db.execute(text("SELECT id FROM wechat_accounts WHERE deleted_at IS NULL LIMIT 1")).scalar())
    uid = int(db.execute(text("SELECT id FROM users LIMIT 1")).scalar())

    pkg = create_package(db, tid, aid, {"name": _name(), "contact_name": "Lin", "wechat_id": "wx", "text_content": "Welcome", "qr_asset_id": 999}, uid)
    pkg.qr_asset_id = 999; db.commit()
    pkg = enable_package(db, tid, pkg.id)
    d = create_delivery(db, tid, None, aid, "openid_1", pkg.id, uid, uuid.uuid4().hex)
    did = d.id
    db.close()
    await execute_delivery(did)
    db2 = MysqlSessionLocal()
    data = get_delivery(db2, tid, did)
    assert data["status"] == "success", f"got {data['status']}"
    assert data["text_status"] == "success"
    assert data["qr_status"] == "success"
    assert len(data["attempts"]) >= 2
    print(f"  PASS: status={data['status']} text={data['text_status']} qr={data['qr_status']} attempts={len(data['attempts'])} mode={data['delivery_mode']}")
    db2.close()
    print("  Scenario 1 PASSED")


async def scenario_2():
    print("\n=== SCENARIO 2: partial fail -> retry qr ===")
    db = MysqlSessionLocal()
    tid = int(db.execute(text("SELECT id FROM tenants LIMIT 1")).scalar())
    aid = int(db.execute(text("SELECT id FROM wechat_accounts WHERE deleted_at IS NULL LIMIT 1")).scalar())
    uid = int(db.execute(text("SELECT id FROM users LIMIT 1")).scalar())

    pkg = create_package(db, tid, aid, {"name": _name(), "contact_name": "Lin", "wechat_id": "wx", "text_content": "Welcome", "qr_asset_id": 999}, uid)
    pkg.qr_asset_id = 999; db.commit()
    pkg = enable_package(db, tid, pkg.id)
    d = create_delivery(db, tid, None, aid, "openid_2", pkg.id, uid, uuid.uuid4().hex)
    did = d.id
    db.close()

    # Mock: make send_image_message throw
    import app.services.wechat_message_service as _ws
    from app.services import wechat_delivery_service as _ds
    print(f"  wms id={id(_ws)} ds._wms id={id(_ds._wms)} same={_ws is _ds._wms}")

    async def _mock_fail(*a, **kw):
        raise RuntimeError("Mock QR failure")

    _orig = _ws.send_image_message
    _ws.send_image_message = _mock_fail
    print(f"  mock active: {_ds._wms.send_image_message is _mock_fail}")
    try:
        await execute_delivery(did)
    finally:
        _ws.send_image_message = _orig
    print(f"  mock restored: {_ds._wms.send_image_message is _orig}")

    db2 = MysqlSessionLocal()
    d2 = db2.query(ContactDelivery).filter(ContactDelivery.id == did).first()
    assert d2.status == "partial_failed", f"Expected partial_failed, got {d2.status}"
    assert d2.text_status == "success"
    assert d2.qr_status == "failed"
    print(f"  Partial fail confirmed: status={d2.status} text={d2.text_status} qr={d2.qr_status}")
    db2.close()

    # Retry only QR
    db3 = MysqlSessionLocal()
    await retry_delivery(db3, tid, did, "qr", uuid.uuid4().hex, uid)
    db3.close()

    db4 = MysqlSessionLocal()
    data = get_delivery(db4, tid, did)
    assert data["status"] == "success", f"Expected success after retry, got {data['status']}"
    assert data["text_status"] == "success"
    assert data["qr_status"] == "success"
    qr_attempts = [a for a in data["attempts"] if a["step"] == "qr"]
    print(f"  After retry: status={data['status']} qr_attempts={len(qr_attempts)}")
    assert len(qr_attempts) >= 2, f"QR attempts {len(qr_attempts)} < 2"
    db4.close()
    print("  Scenario 2 PASSED")


async def main():
    assert settings.wechat_send_mode == "mock", "Need mock mode"
    await scenario_1()
    await scenario_2()
    print("\n=== ALL ACCEPTANCE PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
