"""TaGeAI Integration 输入边界回归测试。

Gateway 是第一层防护，但 Integration API 仍须在创建 ContentJob 前拒绝无法消费的
跨系统资产引用。这样内部服务、运维脚本或未来调用方即使绕过 Gateway，也不会得到
202 Accepted 后才在异步生成阶段失败的假成功结果。
"""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.integrations.tageai.auth import verify_tageai_signature
from app.integrations.tageai import router as tageai_router
from app.integrations.tageai import service as tageai_service
from app.integrations.tageai.service import IntegrationInputError, _validate_create_input


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具，纯输入契约测试不得依赖或修改本机业务数据。"""

    yield


def test_tageai_integration_accepts_url_reference_type():
    """Gateway 对外契约允许的 URL 参考仍可由平台安全抓取并消费。"""

    _validate_create_input(
        operation="imitate",
        delivery_mode="DRAFT",
        input_data={
            "reference": {
                "type": "url",
                "value": "https://example.com/reference",
            }
        },
    )


def test_tageai_integration_requires_budget_approval_before_accepting_extra_media():
    """超过默认图片数量要在入队前返回可交互错误，不能等 Worker 消耗资源后才失败。"""

    with pytest.raises(IntegrationInputError) as exception:
        _validate_create_input(
            operation="generate",
            delivery_mode="PREVIEW",
            input_data={"topic": "AI 内容运营趋势", "image_count": 6},
        )

    assert exception.value.code == "GENERATION_BUDGET_APPROVAL_REQUIRED"
    assert exception.value.details == {
        "requested": {"image_count": 6},
        "default": {"image_count": 5},
        "hard_limit": {"image_count": 8},
    }


def test_tageai_integration_freezes_approved_budget_into_generation_config():
    """一次确认后的预算必须进入当前任务配置，异步 Worker 重试不能回退为默认值。"""

    from app.integrations.tageai.service import _build_generation_config

    config = _build_generation_config(
        "generate",
        "PREVIEW",
        {
            "topic": "AI 内容运营趋势",
            "image_count": 6,
            "video_count": 1,
            "video_duration_seconds": 30,
            "budget_approval": {
                "image_count": 6,
                "video_count": 1,
                "video_duration_seconds": 30,
            },
        },
    )

    assert config["generation_budget"] == {
        "article_character_limit": 5000,
        "image_count": 6,
        "video_count": 1,
        "video_duration_seconds": 30,
        "image_prompt_character_limit": 600,
    }


def test_tageai_integration_rejects_text_reference_type():
    """原文不得经 Gateway 外部调用写入任务快照与审计链路。"""

    with pytest.raises(IntegrationInputError) as exception:
        _validate_create_input(
            operation="imitate",
            delivery_mode="DRAFT",
            input_data={
                "reference": {"type": "text", "value": "不应跨系统传输的整篇原文"},
            },
        )

    assert exception.value.code == "UNSUPPORTED_REFERENCE_TYPE"
    assert str(exception.value) == "当前仿写参考类型暂不支持"


def test_tageai_service_rejects_asset_ref_before_opening_database_session(monkeypatch):
    """asset_ref 必须在服务入口拒绝，不能打开会话、创建 ContentJob 或投递队列。"""

    session_opened = False

    def unexpected_database_session():
        """若校验失效，数据库会话被打开即代表任务可能进入持久化路径。"""

        nonlocal session_opened
        session_opened = True
        raise AssertionError("asset_ref 不应打开数据库会话")

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", unexpected_database_session)

    with pytest.raises(IntegrationInputError) as exception:
        tageai_service.create_invocation(
            invocation_id="tage-inv-asset-ref-service",
            tenant_id=7,
            operation="imitate",
            delivery_mode="DRAFT",
            target_account_ref=None,
            input_data={
                "reference": {"type": "asset_ref", "value": "asset-20260803-001"},
            },
            execution_id="exec-asset-ref-service",
        )

    assert exception.value.code == "UNSUPPORTED_REFERENCE_TYPE"
    assert str(exception.value) == "当前仿写参考类型暂不支持"
    assert session_opened is False


def test_tageai_service_rejects_generate_asset_ref_before_opening_database_session(monkeypatch):
    """生成任务携带外部资产引用时也必须在持久化边界前失败。

    ``reference`` 对生成任务是可选参数，但一旦调用方提供它，平台不能静默忽略无法
    授权解析的 ``asset_ref``。否则 Gateway 会收到 202，而用户实际得到一篇没有使用
    指定素材的文章，既破坏请求语义也延后了故障暴露。
    """

    session_opened = False

    def unexpected_database_session():
        """服务层校验缺失时，打开会话意味着无效输入已进入持久化路径。"""

        nonlocal session_opened
        session_opened = True
        raise AssertionError("generate + asset_ref 不应打开数据库会话")

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", unexpected_database_session)

    with pytest.raises(IntegrationInputError) as exception:
        tageai_service.create_invocation(
            invocation_id="tage-inv-generate-asset-ref-service",
            tenant_id=7,
            operation="generate",
            delivery_mode="DRAFT",
            target_account_ref=None,
            input_data={
                "topic": "AI 内容运营趋势",
                "reference": {"type": "asset_ref", "value": "asset-20260803-002"},
            },
            execution_id="exec-generate-asset-ref-service",
        )

    assert exception.value.code == "UNSUPPORTED_REFERENCE_TYPE"
    assert str(exception.value) == "当前参考类型暂不支持"
    assert session_opened is False


def test_tageai_api_rejects_asset_ref_without_persisting_content_job(monkeypatch):
    """路由必须将服务层拒绝转换为稳定 422 业务响应，且不触发持久化。"""

    session_opened = False

    def unexpected_database_session():
        """路由若错误受理 asset_ref，会在创建任务前尝试打开数据库会话。"""

        nonlocal session_opened
        session_opened = True
        raise AssertionError("asset_ref 不应创建 ContentJob")

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", unexpected_database_session)
    request = tageai_router.CreateInvocationRequest.model_validate({
        "invocationId": "tage-inv-asset-ref-api",
        "tenantBindingId": "binding-7",
        "operation": "imitate",
        "deliveryMode": "DRAFT",
        "targetAccountRef": "tenant-wechat-account-1",
        "ownerUserId": "101",
        "input": {
            "reference": {"type": "asset_ref", "value": "asset-20260803-001"},
        },
        "executionId": "exec-asset-ref-api",
    })

    with pytest.raises(HTTPException) as exception:
        asyncio.run(tageai_router.create(
            request,
            auth_ctx={"tenant_id": 7, "tenant_binding_id": "binding-7"},
        ))

    assert exception.value.status_code == 422
    assert exception.value.detail == {
        "errorCode": "UNSUPPORTED_REFERENCE_TYPE",
        "message": "当前仿写参考类型暂不支持",
        "retryable": False,
    }
    assert session_opened is False


def test_tageai_http_api_rejects_generate_asset_ref_with_stable_error_payload(monkeypatch):
    """真实 HTTP 边界必须返回可供 Gateway 解析的 422 错误体。

    直接调用路由函数只能证明异常对象正确，无法验证 FastAPI 对 ``detail`` 的 JSON
    包装格式。这里使用最小应用和认证依赖替身，覆盖 Gateway 实际接收到的响应。
    """

    def unexpected_database_session():
        raise AssertionError("HTTP API 不应为 generate + asset_ref 打开数据库会话")

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", unexpected_database_session)
    app = FastAPI()
    app.include_router(tageai_router.router, prefix="/integrations/tageai")
    app.dependency_overrides[verify_tageai_signature] = lambda: {
        "tenant_id": 7,
        "tenant_binding_id": "binding-7",
    }

    response = TestClient(app).post("/integrations/tageai/invocations", headers={
        "Idempotency-Key": "idem-generate-asset-ref-http",
    }, json={
        "invocationId": "tage-inv-generate-asset-ref-http",
        "tenantBindingId": "binding-7",
        "operation": "generate",
        "deliveryMode": "DRAFT",
        "targetAccountRef": "tenant-wechat-account-1",
        "ownerUserId": "101",
        "input": {
            "topic": "AI 内容运营趋势",
            "reference": {"type": "asset_ref", "value": "asset-20260803-003"},
        },
        "executionId": "exec-generate-asset-ref-http",
    })

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "errorCode": "UNSUPPORTED_REFERENCE_TYPE",
            "message": "当前参考类型暂不支持",
            "retryable": False,
        }
    }


def test_tageai_create_request_requires_target_account_reference():
    """所有投递请求必须带受控公众号引用，不能由平台默认挑选第一个账号。

    公众号账号属于外部副作用的最终目标。缺少引用时即使当前租户恰好只有一个账号，
    也不能推断用户意图，否则未来新增账号后历史调用会落到错误的公众号。
    """

    with pytest.raises(ValidationError):
        tageai_router.CreateInvocationRequest.model_validate({
            "invocationId": "tage-inv-missing-account",
            "tenantBindingId": "binding-7",
            "operation": "generate",
            "deliveryMode": "DRAFT",
            "input": {"topic": "AI 内容运营趋势"},
            "executionId": "exec-missing-account",
        })


def test_tageai_target_account_binding_resolves_only_registered_reference():
    """账号引用必须由服务端绑定表解析，不能把任意字符串或本地 ID 当作可信目标。"""

    bindings = {
        "tenant-wechat-account-1": 101,
        "tenant-wechat-account-2": 102,
    }

    assert tageai_service.resolve_target_account_binding(
        tenant_binding_id="binding-7",
        target_account_ref="tenant-wechat-account-2",
        target_account_bindings=bindings,
    ) == 102

    with pytest.raises(IntegrationInputError) as missing_reference:
        tageai_service.resolve_target_account_binding(
            tenant_binding_id="binding-7",
            target_account_ref="",
            target_account_bindings=bindings,
        )
    assert missing_reference.value.code == "ACCOUNT_NOT_BOUND"

    with pytest.raises(IntegrationInputError) as unknown_reference:
        tageai_service.resolve_target_account_binding(
            tenant_binding_id="binding-7",
            target_account_ref="101",
            target_account_bindings=bindings,
        )
    assert unknown_reference.value.code == "ACCOUNT_NOT_BOUND"


def test_tageai_service_rejects_unknown_account_before_opening_database_session(monkeypatch):
    """未知账号引用不得创建 ContentJob，连数据库会话也不应进入。

    该测试锁定创建流程的失败顺序：先解析可信绑定，再打开持久化会话。这样错误配置、
    跨租户引用或客户端伪造不会留下半创建任务，更不会被异步 Worker 继续消费。
    """

    session_opened = False

    def unexpected_database_session():
        """数据库会话一旦被打开，即表示不可信目标已越过输入边界。"""

        nonlocal session_opened
        session_opened = True
        raise AssertionError("未知公众号引用不应创建 ContentJob")

    monkeypatch.setattr(tageai_service, "MysqlSessionLocal", unexpected_database_session)

    with pytest.raises(IntegrationInputError) as exception:
        tageai_service.create_invocation(
            invocation_id="tage-inv-unknown-account",
            tenant_id=7,
            tenant_binding_id="binding-7",
            target_account_bindings={"tenant-wechat-account-1": 101},
            operation="generate",
            delivery_mode="DRAFT",
                target_account_ref="tenant-wechat-account-unknown",
                input_data={"topic": "AI 内容运营趋势"},
                execution_id="exec-unknown-account",
                idempotency_key="idem-unknown-account",
            )

    assert exception.value.code == "ACCOUNT_NOT_BOUND"
    assert session_opened is False
