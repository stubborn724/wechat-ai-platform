"""P1 测试配置"""
import pytest
from app.database import MysqlSessionLocal


@pytest.fixture(scope="function")
def db():
    """每个测试独立 session"""
    session = MysqlSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="function")
def test_tenant(db):
    """获取第一个可用租户"""
    from app.models.mysql_models import Tenant
    tenant = db.query(Tenant).first()
    assert tenant, "数据库至少需要一个租户"
    return tenant


@pytest.fixture(scope="function")
def test_account(db, test_tenant):
    """获取第一个可用公众号"""
    from app.models.mysql_models import WeChatAccount
    account = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == test_tenant.id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    assert account, "至少需要一个公众号"
    return account


@pytest.fixture(scope="function")
def test_user(db, test_tenant):
    """获取第一个可用用户"""
    from app.models.mysql_models import User
    user = db.query(User).first()
    assert user, "至少需要一个用户"
    return user
