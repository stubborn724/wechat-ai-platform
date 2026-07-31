"""P1 测试配置"""
import pytest
from uuid import uuid4
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


@pytest.fixture(autouse=True)
def reset_test_tables(db):
    """每个测试开始前清空业务表，保证重复运行时也不会被历史数据污染。"""
    from app.models.mysql_models import (
        Article,
        CommentLead,
        ConversationMessage,
        ContactDelivery,
        ContactDeliveryAttempt,
        ContactPackage,
        Membership,
        PublishAttempt,
        PublishPlan,
        PublishSchedule,
        Review,
        SyncJob,
        Tenant,
        User,
        WeChatAccount,
        WeChatComment,
        WeChatCommentAutoConfig,
        WeChatMessage,
        WeChatSyncedArticle,
        WeChatUserInteraction,
        WechatMediaAsset,
        ContentJob,
    )

    tables = [
        ContactDeliveryAttempt,
        ContactDelivery,
        CommentLead,
        ConversationMessage,
        WeChatMessage,
        WeChatComment,
        WeChatCommentAutoConfig,
        WeChatUserInteraction,
        WechatMediaAsset,
        SyncJob,
        Review,
        PublishAttempt,
        PublishSchedule,
        PublishPlan,
        ContentJob,
        WeChatSyncedArticle,
        Article,
        ContactPackage,
        WeChatAccount,
        Membership,
        User,
        Tenant,
    ]

    for model in tables:
        db.query(model).delete(synchronize_session=False)
    db.commit()
    yield


@pytest.fixture(scope="function")
def test_tenant(db):
    """获取第一个可用租户"""
    from app.models.mysql_models import Tenant
    tenant = db.query(Tenant).first()
    if tenant:
        return tenant

    tenant = Tenant(name="测试租户", slug=f"test-{uuid4().hex[:8]}")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@pytest.fixture(scope="function")
def test_account(db, test_tenant):
    """获取第一个可用公众号"""
    from app.models.mysql_models import WeChatAccount
    account = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == test_tenant.id,
        WeChatAccount.deleted_at.is_(None),
    ).first()
    if account:
        return account

    account = WeChatAccount(
        tenant_id=test_tenant.id,
        name="测试公众号",
        app_id=f"wx{uuid4().hex[:16]}",
        auth_mode="credential",
        status="active",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@pytest.fixture(scope="function")
def test_user(db, test_tenant):
    """获取第一个可用用户"""
    from app.models.mysql_models import User
    user = db.query(User).first()
    if user:
        return user

    user = User(
        email=f"tester-{uuid4().hex[:8]}@example.com",
        password_hash="hashed-password",
        display_name="测试用户",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
