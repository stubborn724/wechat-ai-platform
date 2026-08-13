"""TaGeAI 微信连接器账户服务测试。

连接器 API 是 TaGeAI SaaS 写入公众号凭据的唯一入口。测试只覆盖纯服务边界，
不访问真实微信开放平台或本机数据库，确保凭据不会被响应、日志或调用方状态携带。
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """连接器摘要测试是纯函数测试，不能因全局 MySQL 清理夹具而依赖历史业务表。"""

    yield


def test_connector_summary_never_returns_app_secret():
    """连接器摘要只能向 TaGeAI 返回可调度账号，不得泄露任何凭据字段。"""

    from app.integrations.tageai.connector_service import build_connector_account_summary

    account = SimpleNamespace(
        id=12,
        name="企业服务号",
        status="active",
        capabilities={
            "tageai_connector_ref": "wechat-account-12",
            "delivery_modes": ["DRAFT", "PUBLISH"],
        },
        last_health_at=None,
        last_health_error=None,
    )

    result = build_connector_account_summary(account)

    assert result == {
        "accountRef": "wechat-account-12",
        "displayName": "企业服务号",
        "status": "ACTIVE",
        "capabilities": ["DRAFT", "PUBLISH"],
        "lastVerifiedAt": None,
        "errorCode": None,
        "errorMessage": None,
    }
    assert "appSecret" not in result
    assert "encrypted_secret" not in result


def test_connector_summary_rejects_account_without_tageai_reference():
    """历史公众号没有连接器引用时，不能被主 Agent 误选为 SaaS 投递目标。"""

    from app.integrations.tageai.connector_service import ConnectorAccountInputError
    from app.integrations.tageai.connector_service import build_connector_account_summary

    account = SimpleNamespace(
        id=13,
        name="旧公众号",
        status="active",
        capabilities={"delivery_modes": ["DRAFT"]},
        last_health_at=None,
        last_health_error=None,
    )

    with pytest.raises(ConnectorAccountInputError) as exception:
        build_connector_account_summary(account)

    assert exception.value.code == "CONNECTOR_REFERENCE_MISSING"


def test_connector_account_ref_must_be_stable_non_numeric_public_identifier():
    """调用方引用不能退化为内部自增主键，避免跨租户猜测或迁移后误投递。"""

    from app.integrations.tageai.connector_service import ConnectorAccountInputError
    from app.integrations.tageai.connector_service import validate_connector_account_ref

    assert validate_connector_account_ref("wechat-account-a71f") == "wechat-account-a71f"

    with pytest.raises(ConnectorAccountInputError) as exception:
        validate_connector_account_ref("12")

    assert exception.value.code == "INVALID_CONNECTOR_ACCOUNT_REF"


def test_connector_create_route_returns_only_safe_account_summary(monkeypatch):
    """Integration 路由可创建账号，但传输层不得把提交的 AppSecret 反射回响应。"""

    from app.integrations.tageai import connector_service
    from app.integrations.tageai import router

    request = router.CreateConnectorAccountRequest.model_validate({
        "connectorAccountRef": "wechat-account-a71f",
        "ownerUserId": "101",
        "displayName": "企业服务号",
        "appId": "wx-test-app",
        "appSecret": "not-for-response",
    })
    monkeypatch.setattr(connector_service, "upsert_connector_account", lambda *_args, **_kwargs: {
        "accountRef": "wechat-account-a71f",
        "displayName": "企业服务号",
        "status": "ACTIVE",
        "capabilities": ["DRAFT", "PUBLISH"],
        "lastVerifiedAt": None,
        "errorCode": None,
        "errorMessage": None,
    })

    fake_db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    result = router.create_connector_account(
        request=request,
        auth_ctx={"tenant_id": 7, "tenant_binding_id": "binding-7"},
        db=fake_db,
    )

    assert result["accountRef"] == "wechat-account-a71f"
    assert "appSecret" not in result


def test_connector_account_binding_resolves_only_current_tenant_account():
    """执行链路只能把当前租户的连接器逻辑引用转换为内部账号 ID。"""

    from app.integrations.tageai.connector_service import resolve_connector_account_id

    account = SimpleNamespace(
        id=42,
        tenant_id=7,
        deleted_at=None,
        capabilities={
            "tageai_connector_ref": "wechat-account-a71f",
            "tageai_connector_owner_user_id": "101",
        },
    )

    assert resolve_connector_account_id(
        [account], tenant_id=7, owner_user_id="101", account_ref="wechat-account-a71f",
    ) == 42
    assert resolve_connector_account_id(
        [account], tenant_id=8, owner_user_id="101", account_ref="wechat-account-a71f",
    ) is None


def test_connector_account_binding_resolves_only_its_owner_inside_same_tenant():
    """同一企业的其他员工不能借由账号引用使用个人公众号。"""

    from app.integrations.tageai.connector_service import resolve_connector_account_id

    account = SimpleNamespace(
        id=42,
        tenant_id=7,
        deleted_at=None,
        capabilities={
            "tageai_connector_ref": "wechat-account-a71f",
            "tageai_connector_owner_user_id": "101",
        },
    )

    assert resolve_connector_account_id(
        [account], tenant_id=7, owner_user_id="101", account_ref="wechat-account-a71f",
    ) == 42
    assert resolve_connector_account_id(
        [account], tenant_id=7, owner_user_id="202", account_ref="wechat-account-a71f",
    ) is None


def test_invocation_accepts_connector_binding_when_static_mapping_is_absent():
    """连接器上线后，调用不再要求环境变量维护每个账号的内部数据库 ID。"""

    from app.integrations.tageai.service import resolve_target_account_binding

    account_id = resolve_target_account_binding(
        tenant_binding_id="binding-7",
        target_account_ref="wechat-account-a71f",
        target_account_bindings=None,
        connector_account_lookup=lambda account_ref: 42 if account_ref == "wechat-account-a71f" else None,
    )

    assert account_id == 42


def test_invocation_lookup_reads_personal_connector_reference_inside_current_tenant():
    """真实 Invocation 只解析当前用户在当前租户拥有的连接器账号。"""

    from app.integrations.tageai.service import lookup_connector_account_id

    account = SimpleNamespace(
        id=42,
        tenant_id=7,
        deleted_at=None,
        capabilities={
            "tageai_connector_ref": "wechat-account-a71f",
            "tageai_connector_owner_user_id": "101",
        },
    )

    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return [account]

    class Db:
        def query(self, *_args):
            return Query()

    assert lookup_connector_account_id(
        Db(), tenant_id=7, owner_user_id="101", account_ref="wechat-account-a71f",
    ) == 42
    assert lookup_connector_account_id(
        Db(), tenant_id=7, owner_user_id="202", account_ref="wechat-account-a71f",
    ) is None
