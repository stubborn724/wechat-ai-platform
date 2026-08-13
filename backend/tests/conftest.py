"""P1 测试配置。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config import settings
from app.database import MysqlBase, MysqlSessionLocal


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
    """每个测试开始前清空全部 ORM 业务表，避免新增模型漏清理。

    这里不能继续维护一份手工模型列表：图片归档链路新增 ``assets``、``asset_usages``
    等表后，旧列表虽然能清掉大多数任务数据，却会在删除 ``tenants`` 时被遗留的资产
    外键拦截。测试夹具应覆盖整个 MySQL ORM 元数据，因此按照 SQLAlchemy 的依赖排序
    反向删除所有映射表；外键检查只在这个测试清理窗口内关闭，生产代码和生产数据库
    的约束行为完全不受影响。"""
    # 保护真实开发库：测试清理是破坏性操作，必须显式在测试数据库上开启。过去仅凭
    # ``environment=development`` 运行 pytest 会直接清空本地租户和定时任务，属于无法
    # 接受的测试基础设施风险。CI 或本地专用测试库应设置 ``ALLOW_TEST_DB_RESET=1``，并
    # 使用名称包含 ``test``/``pytest`` 的数据库；生产 Worker 不会加载本测试夹具。
    database_name = str(settings.mysql_database).lower()
    explicitly_allowed = os.getenv("ALLOW_TEST_DB_RESET") == "1"
    if not explicitly_allowed or not any(marker in database_name for marker in ("test", "pytest")):
        raise RuntimeError(
            "拒绝清理非测试数据库：请设置 ALLOW_TEST_DB_RESET=1，并将 MYSQL_DATABASE "
            "指向名称包含 test 或 pytest 的独立库。"
        )

    # 导入完整模型模块，确保所有声明都已经注册到 MysqlBase.metadata。这里只是注册
    # ORM 类，不会触发业务查询，也不会改变应用运行时的数据库连接配置。
    import app.models.mysql_models  # noqa: F401

    try:
        # 关闭当前 MySQL 连接的外键检查，允许存在循环引用或尚未纳入依赖排序的
        # 历史表一并清空。SET 是 session 级变量，不会影响其他 Worker 连接。
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in reversed(MysqlBase.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    except Exception:
        # 清理失败时回滚本次删除，避免污染后续测试，并把连接恢复到可复用状态。
        db.rollback()
        raise
    finally:
        # 无论清理成功还是失败，都恢复外键约束；连接回池后不能留下关闭检查的状态。
        db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
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
