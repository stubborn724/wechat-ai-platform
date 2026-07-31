import hashlib
import hmac
import json

import pytest
import requests


@pytest.fixture(autouse=True)
def reset_test_tables():
    """该文件只验证中转站 HTTP 客户端，不触碰数据库，避免全局清理夹具引入外部状态。"""
    yield


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({
            "url": url,
            "data": data,
            "headers": headers or {},
            "timeout": timeout,
        })
        return _FakeResponse(self.payload)


class _ValidationErrorResponse:
    """模拟中转站返回的字段校验错误，保留响应体便于验证诊断信息。"""

    status_code = 422
    text = '{"detail":[{"loc":["publishPayload","coverImageUrl"],"msg":"Field required"}]}'

    def raise_for_status(self):
        raise requests.HTTPError("422 Client Error", response=self)

    def json(self):
        return json.loads(self.text)


class _ValidationErrorSession:
    """只返回 422 的会话桩，避免测试向真实中转站发请求。"""

    def __init__(self):
        self.trust_env = True
        self.proxies = {}

    def post(self, url, data=None, headers=None, timeout=None):
        return _ValidationErrorResponse()


def test_relay_client_signs_raw_json_body_and_preserves_publish_payload():
    from app.services.wechat_relay_client import WeChatRelayClient

    session = _FakeSession({
        "success": True,
        "status": "DRAFT_CREATED",
        "wechatArticleId": "draft-media-id",
        "wechatUrl": "",
        "message": "ok",
    })
    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=session,
        nonce_factory=lambda: "nonce-001",
        timestamp_factory=lambda: "1760000000",
    )

    result = client.publish_article(
        app_id="wx_app",
        app_secret="wx_secret",
        request_id="tenant-article-1-draft",
        tenant_id="tenant-1",
        publish_mode="draft",
        confirm_publish=False,
        title="文章标题",
        digest="摘要",
        html="<p>正文</p>",
        cover_image_url="https://assets.example.com/cover.png",
        author="作者",
    )

    assert result["media_id"] == "draft-media-id"
    assert result["relay_status"] == "DRAFT_CREATED"

    call = session.calls[0]
    assert call["url"] == "http://relay.example.com/api/wechat/articles/publish"
    assert call["headers"]["X-Relay-App-Id"] == "relay_client"
    assert call["headers"]["X-Relay-Timestamp"] == "1760000000"
    assert call["headers"]["X-Relay-Nonce"] == "nonce-001"

    body = json.loads(call["data"].decode("utf-8"))
    assert body["wechatCredential"] == {
        "appId": "wx_app",
        "appSecret": "wx_secret",
    }
    assert body["publishPayload"]["publishMode"] == "draft_only"
    assert body["publishPayload"]["confirmPublish"] is False
    assert body["publishPayload"]["html"] == "<p>正文</p>"
    assert body["publishPayload"]["contentSourceUrl"] is None

    body_hash = hashlib.sha256(call["data"]).hexdigest()
    signature_payload = "\n".join([
        "POST",
        "/api/wechat/articles/publish",
        "1760000000",
        "nonce-001",
        body_hash,
    ])
    expected_signature = hmac.new(
        b"relay_secret",
        signature_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert call["headers"]["X-Relay-Signature"] == expected_signature


def test_relay_client_uses_extended_default_timeout_for_image_publish():
    """多图发布需等待中转站下载图片并上传微信，默认超时不能只有 30 秒。"""
    from app.services.wechat_relay_client import WeChatRelayClient

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=_FakeSession({}),
    )

    assert client.timeout == 180


def test_relay_client_maps_direct_publish_to_public_publish_with_confirm_gate():
    from app.services.wechat_relay_client import WeChatRelayClient

    session = _FakeSession({
        "success": True,
        "status": "PUBLIC_PUBLISH_SUBMITTED",
        "wechatArticleId": "publish-id-001",
        "wechatUrl": "",
        "message": "submitted",
    })
    client = WeChatRelayClient(
        base_url="http://relay.example.com/",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=session,
        nonce_factory=lambda: "nonce-002",
        timestamp_factory=lambda: "1760000001",
    )

    result = client.publish_article(
        app_id="wx_app",
        app_secret="wx_secret",
        request_id="tenant-article-1-direct",
        tenant_id="tenant-1",
        publish_mode="direct",
        confirm_publish=True,
        title="文章标题",
        digest="摘要",
        html="<p>正文</p>",
        cover_image_url="https://assets.example.com/cover.png",
    )

    body = json.loads(session.calls[0]["data"].decode("utf-8"))
    assert body["publishPayload"]["publishMode"] == "public_publish"
    assert body["publishPayload"]["confirmPublish"] is True
    assert result["publish_id"] == "publish-id-001"
    assert result["relay_status"] == "PUBLIC_PUBLISH_SUBMITTED"


def test_relay_client_keeps_author_empty_when_business_does_not_configure_one():
    """系统不得再给客户公众号自动写入“AI 运营平台”作者字段。"""
    from app.services.wechat_relay_client import WeChatRelayClient

    session = _FakeSession({
        "success": True,
        "status": "DRAFT_CREATED",
        "wechatArticleId": "draft-media-id",
    })
    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=session,
    )

    client.publish_article(
        app_id="wx_app",
        app_secret="wx_secret",
        request_id="article-empty-author",
        tenant_id="tenant-1",
        publish_mode="draft",
        confirm_publish=False,
        title="文章标题",
        digest="摘要",
        html="<p>正文</p>",
        cover_image_url="https://assets.example.com/cover.png",
    )

    body = json.loads(session.calls[0]["data"].decode("utf-8"))
    assert body["publishPayload"]["author"] == ""


def test_relay_client_blocks_direct_publish_without_explicit_confirmation():
    from app.services.wechat_relay_client import WeChatRelayClient

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=_FakeSession({}),
    )

    with pytest.raises(ValueError, match="confirm_publish=True"):
        client.publish_article(
            app_id="wx_app",
            app_secret="wx_secret",
            request_id="tenant-article-1-direct",
            tenant_id="tenant-1",
            publish_mode="direct",
            confirm_publish=False,
            title="文章标题",
            digest="摘要",
            html="<p>正文</p>",
            cover_image_url="https://assets.example.com/cover.png",
        )


def test_relay_client_reports_validation_response_body_without_request_secrets():
    """中转站字段校验失败时，应暴露字段位置，便于修复协议不匹配。"""
    from app.services.wechat_relay_client import WeChatRelayClient

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=_ValidationErrorSession(),
    )

    with pytest.raises(RuntimeError, match="coverImageUrl.*Field required"):
        client.publish_article(
            app_id="wx_app",
            app_secret="wx_secret",
            request_id="tenant-article-1-draft",
            tenant_id="tenant-1",
            publish_mode="draft",
            confirm_publish=False,
            title="文章标题",
            digest="摘要",
            html="<p>正文</p>",
            cover_image_url="https://assets.example.com/cover.png",
        )
