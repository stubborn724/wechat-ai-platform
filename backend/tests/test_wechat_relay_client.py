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
            "method": "POST",
            "url": url,
            "data": data,
            "headers": headers or {},
            "timeout": timeout,
        })
        return _FakeResponse(self.payload)

    def get(self, url, headers=None, timeout=None):
        """模拟状态查询请求，并保留方法和空请求体以验证不会再次发布。"""

        self.calls.append({
            "method": "GET",
            "url": url,
            "data": b"",
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


def test_relay_client_queries_publish_status_with_hmac_get_without_republishing():
    """发布状态读取只查询中转站，不能把同一篇文章再次提交到微信。"""

    from app.services.wechat_relay_client import WeChatRelayClient

    session = _FakeSession({
        "success": True,
        "status": "PUBLISHED",
        "wechatArticleId": "wechat-article-001",
        "wechatUrl": "https://mp.weixin.qq.com/s/example",
        "message": "published",
    })
    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=session,
        nonce_factory=lambda: "nonce-status-001",
        timestamp_factory=lambda: "1760000003",
    )

    result = client.query_publish_status("publish-id-001")

    assert result == {
        "relay_status": "PUBLISHED",
        "wechat_article_id": "wechat-article-001",
        "wechat_url": "https://mp.weixin.qq.com/s/example",
        "message": "published",
        "error_code": None,
        "raw": session.payload,
    }
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://relay.example.com/api/wechat/articles/publish/publish-id-001"
    assert call["data"] == b""
    assert call["headers"]["X-Relay-App-Id"] == "relay_client"

    expected_signature = hmac.new(
        b"relay_secret",
        "\n".join([
            "GET",
            "/api/wechat/articles/publish/publish-id-001",
            "1760000003",
            "nonce-status-001",
            hashlib.sha256(b"").hexdigest(),
        ]).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert call["headers"]["X-Relay-Signature"] == expected_signature


def test_relay_client_maps_private_direct_publish_to_follower_push():
    """私域直接发布必须调用中转站的粉丝群发模式并返回 msg_id。"""
    from app.services.wechat_relay_client import WeChatRelayClient

    session = _FakeSession({
        "success": True,
        "status": "FOLLOWER_PUSH_SENT",
        "wechatArticleId": "msg-id-001",
        "wechatUrl": "",
        "message": "sent",
    })
    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=session,
        nonce_factory=lambda: "nonce-private-001",
        timestamp_factory=lambda: "1760000002",
    )

    result = client.publish_article(
        app_id="wx_app",
        app_secret="wx_secret",
        request_id="tenant-article-1-private",
        tenant_id="tenant-1",
        publish_mode="direct",
        publish_domain="private",
        confirm_publish=True,
        title="文章标题",
        digest="摘要",
        html="<p>正文</p>",
        cover_image_url="https://assets.example.com/cover.png",
    )

    body = json.loads(session.calls[0]["data"].decode("utf-8"))
    assert body["publishPayload"]["publishMode"] == "follower_push"
    assert body["publishPayload"]["confirmPublish"] is True
    assert result["msg_id"] == "msg-id-001"
    assert result["relay_status"] == "FOLLOWER_PUSH_SENT"


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


def test_relay_client_marks_temporary_draft_rejection_retryable():
    """草稿尚未创建时收到明确的临时拒绝，可安全交给定时任务重试。"""
    from app.services.wechat_relay_client import (
        WeChatRelayClient,
        WechatRelayRetryableError,
    )

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=_FakeSession({
            "success": False,
            "status": "TEMPORARY_UNAVAILABLE",
            "message": "服务暂不可用，请稍后重试",
        }),
    )

    with pytest.raises(WechatRelayRetryableError):
        client.publish_article(
            app_id="wx_app",
            app_secret="wx_secret",
            request_id="draft-retryable-error",
            tenant_id="tenant-1",
            publish_mode="draft",
            confirm_publish=False,
            title="文章标题",
            digest="摘要",
            html="<p>正文</p>",
            cover_image_url="https://assets.example.com/cover.png",
        )


def test_relay_client_never_marks_masssend_quota_exhaustion_retryable():
    """微信 45028 是当天群发额度耗尽，等待几分钟不会恢复，不能自动重试。"""
    from app.services.wechat_relay_client import WeChatRelayClient
    from app.tasks.scheduled_task_executor import is_retryable_scheduled_error

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=_FakeSession({
            "success": False,
            "status": "FAILED",
            "message": "45028 has no masssend quota",
        }),
    )

    with pytest.raises(RuntimeError) as error_info:
        client.publish_article(
            app_id="wx_app",
            app_secret="wx_secret",
            request_id="private-quota-error",
            tenant_id="tenant-1",
            publish_mode="direct",
            publish_domain="private",
            confirm_publish=True,
            title="文章标题",
            digest="摘要",
            html="<p>正文</p>",
            cover_image_url="https://assets.example.com/cover.png",
        )

    assert is_retryable_scheduled_error(error_info.value) is False


def test_relay_client_marks_direct_publish_transport_failure_ambiguous():
    """直接发布遇到连接中断时不能自动重发，否则微信可能已经收到原请求。"""
    from app.services.wechat_relay_client import (
        WeChatRelayClient,
        WechatRelayPublishAmbiguousError,
    )
    from app.tasks.scheduled_task_executor import is_retryable_scheduled_error

    class FailingSession:
        """模拟请求已发出后连接被中断，无法判断中转站是否已受理。"""

        trust_env = False
        proxies = {}

        def post(self, *_args, **_kwargs):
            raise requests.ConnectionError("connection reset")

    client = WeChatRelayClient(
        base_url="http://relay.example.com",
        relay_app_id="relay_client",
        relay_secret="relay_secret",
        session=FailingSession(),
    )

    with pytest.raises(WechatRelayPublishAmbiguousError) as error_info:
        client.publish_article(
            app_id="wx_app",
            app_secret="wx_secret",
            request_id="direct-ambiguous-error",
            tenant_id="tenant-1",
            publish_mode="direct",
            confirm_publish=True,
            title="文章标题",
            digest="摘要",
            html="<p>正文</p>",
            cover_image_url="https://assets.example.com/cover.png",
        )

    assert is_retryable_scheduled_error(error_info.value) is False
