"""TaGeAI Integration HMAC 防重放与请求绑定测试。"""

import asyncio
from time import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request


class _NonceStore:
    """模拟 Redis 的原子 ``SET NX EX``，用于验证多进程共享的 nonce 语义。"""

    def __init__(self):
        """保存已声明的键和值，避免测试依赖本机 Redis 服务。"""

        self.values = {}
        self.calls = []

    def set(self, key, value, *, nx, ex):
        """仅在键不存在时成功，模拟 Redis 的单命令原子去重行为。"""

        self.calls.append((key, value, nx, ex))
        if key in self.values:
            return False
        self.values[key] = value
        return True


def _request(method: str, path: str, query_string: bytes = b"") -> Request:
    """构造带稳定路径和查询串的最小 ASGI 请求，不读取网络或真实服务。"""

    async def receive():
        """提供空请求体，使 GET/POST 签名测试可显式控制 body hash。"""

        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
    )


def test_hmac_signature_binds_method_path_query_and_body():
    """同一 body 的签名不能跨方法、路径或查询参数重放。"""

    from app.integrations.tageai import auth

    common = {
        "signing_secret": "test-secret",
        "client_id": "tage-client-7",
        "timestamp": "1785727800",
        "nonce": "nonce-request-bound",
        "body_bytes": b'{"operation":"generate"}',
    }
    query_signature = auth._build_signature(
        **common,
        method="GET",
        canonical_path="/api/v1/integrations/tageai/invocations/71",
        canonical_query="include=result",
    )
    cancel_signature = auth._build_signature(
        **common,
        method="POST",
        canonical_path="/api/v1/integrations/tageai/invocations/71/cancel",
        canonical_query="",
    )

    assert query_signature != cancel_signature


def test_nonce_claim_uses_redis_set_nx_ex_without_process_local_fallback():
    """重复 nonce 必须由共享 Redis 原子拒绝，而不是依赖单个 Web 进程内存。"""

    from app.integrations.tageai import auth

    store = _NonceStore()

    assert auth.claim_tageai_nonce(store, "tage-client-7", "nonce-1") is True
    assert auth.claim_tageai_nonce(store, "tage-client-7", "nonce-1") is False
    assert store.calls == [
        ("tageai:integration:nonce:tage-client-7:nonce-1", "1", True, auth.TIMESTAMP_WINDOW_SECONDS),
        ("tageai:integration:nonce:tage-client-7:nonce-1", "1", True, auth.TIMESTAMP_WINDOW_SECONDS),
    ]


def test_signature_for_query_cannot_authorize_cancel_route(monkeypatch):
    """捕获到的查询签名不能被重放到同一调用的取消路由。"""

    from app.integrations.tageai import auth

    client_config = {
        "client_id": "tage-client-7",
        "signing_secret": "test-secret",
        "tenant_binding_id": "binding-7",
        "tenant_id": 7,
    }
    timestamp = str(int(time()))
    nonce = "nonce-query-cannot-cancel"
    query_signature = auth._build_signature(
        signing_secret=client_config["signing_secret"],
        client_id=client_config["client_id"],
        method="GET",
        canonical_path="/api/v1/integrations/tageai/invocations/71",
        canonical_query="",
        timestamp=timestamp,
        nonce=nonce,
        body_bytes=b"",
    )
    monkeypatch.setattr(auth, "_find_client_config", lambda _client_id: client_config)
    monkeypatch.setattr(auth, "_get_nonce_store", lambda: _NonceStore(), raising=False)

    with pytest.raises(HTTPException) as exception:
        asyncio.run(auth.verify_tageai_signature(
            request=_request("POST", "/api/v1/integrations/tageai/invocations/71/cancel"),
            x_tageai_client_id="tage-client-7",
            x_tageai_timestamp=timestamp,
            x_tageai_nonce=nonce,
            x_tageai_signature=f"sha256={query_signature}",
        ))

    assert exception.value.status_code == 401
    assert exception.value.detail == "Invalid signature"
