"""微信发布前本地图片 COS 中转测试。"""

import base64
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """中转准备测试只使用内存替身，不访问数据库。"""
    yield


def test_local_minio_images_are_staged_once_and_replaced_with_https():
    """正文与封面重复引用同一本地图时应只上传一次并全部替换。"""
    from app.services.wechat_relay_image_service import WeChatRelayImageService

    local_url = "http://localhost:9002/wechat-assets/assets/107/footer.png"
    public_url = "https://dashscope.example.com/generated.png?signature=1"
    html = (
        f'<p><img src="{public_url}"></p>'
        f"<p><img src='{local_url}'></p>"
        f'<p><img src="{local_url}"></p>'
    )

    class FakeStorage:
        """记录读取键，验证 URL 解析边界。"""

        def __init__(self):
            self.downloaded_keys = []

        def download_bytes(self, object_key):
            self.downloaded_keys.append(object_key)
            return b"local-image"

    class FakeRelay:
        """返回固定签名地址并记录上传参数。"""

        def __init__(self):
            self.stage_calls = []

        def stage_bytes(self, **kwargs):
            self.stage_calls.append(kwargs)
            return SimpleNamespace(
                object_key="temporary/107/wechat-22/footer.png",
                signed_url="https://cos.example.com/signed-footer",
            )

    storage = FakeStorage()
    relay = FakeRelay()
    service = WeChatRelayImageService(
        storage=storage,
        relay=relay,
        minio_public_endpoint="http://localhost:9002",
        minio_bucket="wechat-assets",
    )

    result = service.prepare(
        html=html,
        cover_image_url=local_url,
        tenant_id=107,
        article_id=22,
    )

    assert public_url in result.html
    assert local_url not in result.html
    assert result.html.count("https://cos.example.com/signed-footer") == 2
    assert result.cover_image_url == "https://cos.example.com/signed-footer"
    assert result.object_keys == ["temporary/107/wechat-22/footer.png"]
    assert storage.downloaded_keys == ["assets/107/footer.png"]
    assert len(relay.stage_calls) == 1
    assert relay.stage_calls[0]["data"] == b"local-image"
    assert relay.stage_calls[0]["content_type"] == "image/png"


def test_unrelated_http_image_is_not_treated_as_local_minio():
    """外部 HTTP 图片不得被误解析为本地对象键，应留给发布校验明确拒绝。"""
    from app.services.wechat_relay_image_service import WeChatRelayImageService

    service = WeChatRelayImageService(
        storage=SimpleNamespace(download_bytes=lambda key: b"unexpected"),
        relay=SimpleNamespace(stage_bytes=lambda **kwargs: None),
        minio_public_endpoint="http://localhost:9002",
        minio_bucket="wechat-assets",
    )
    external_url = "http://external.example.com/image.png"

    result = service.prepare(
        html=f'<img src="{external_url}">',
        cover_image_url="https://example.com/cover.png",
        tenant_id=107,
        article_id=22,
    )

    assert external_url in result.html
    assert result.object_keys == []


def test_historical_localhost_url_is_staged_when_worker_uses_internal_minio_endpoint():
    """Worker 使用容器地址时，历史本机 MinIO 地址仍必须进入 COS 中转。

    文章可能由宿主机后端生成并保存 ``localhost:9002`` 地址，而发布 Worker
    在 Docker 中通过 ``minio:9000`` 访问同一个桶。两者只是访问入口不同，不能
    因为当前进程的公共地址发生变化就把历史本地图片漏给微信中转站。
    """
    from app.services.wechat_relay_image_service import WeChatRelayImageService

    local_url = "http://localhost:9002/wechat-assets/assets/107/footer.png"

    class FakeStorage:
        """记录对象键，确保别名解析后仍从 MinIO 下载正确对象。"""

        def __init__(self):
            self.downloaded_keys = []

        def download_bytes(self, object_key):
            self.downloaded_keys.append(object_key)
            return b"local-image"

    class FakeRelay:
        """返回固定 COS 地址，隔离真实云服务。"""

        def __init__(self):
            self.stage_calls = []

        def stage_bytes(self, **kwargs):
            self.stage_calls.append(kwargs)
            return SimpleNamespace(
                object_key="temporary/107/wechat-24/footer.png",
                signed_url="https://cos.example.com/signed-footer",
            )

    storage = FakeStorage()
    relay = FakeRelay()
    service = WeChatRelayImageService(
        storage=storage,
        relay=relay,
        minio_public_endpoint="http://minio:9000",
        minio_bucket="wechat-assets",
        minio_url_aliases=("http://localhost:9002",),
    )

    result = service.prepare(
        html=f'<p>页脚</p><img alt="二维码" src="{local_url}">',
        cover_image_url=local_url,
        tenant_id=107,
        article_id=24,
    )

    assert local_url not in result.html
    assert result.cover_image_url == "https://cos.example.com/signed-footer"
    assert storage.downloaded_keys == ["assets/107/footer.png"]
    assert len(relay.stage_calls) == 1


def test_local_image_uses_detected_mime_when_storage_extension_is_wrong():
    """中转必须使用真实字节格式，不能盲信存储对象后缀或错误响应头。"""
    from app.services.wechat_relay_image_service import WeChatRelayImageService

    # 这是有效的 1x1 JPEG。实际问题中豆包返回 JPEG 字节但对象被命名为 .png，
    # 若按扩展名发送 image/png，微信会在下载校验阶段拒绝该图片。
    jpeg_bytes = base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Ap//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/Iaf/2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
    )

    class FakeRelay:
        """捕获实际发送到 COS 的 MIME，避免依赖真实云存储。"""

        def __init__(self):
            self.stage_calls = []

        def stage_bytes(self, **kwargs):
            self.stage_calls.append(kwargs)
            return SimpleNamespace(
                object_key="temporary/107/wechat-23/generated.jpg",
                signed_url="https://cos.example.com/signed-generated",
            )

    relay = FakeRelay()
    service = WeChatRelayImageService(
        storage=SimpleNamespace(download_bytes=lambda _key: jpeg_bytes),
        relay=relay,
        minio_public_endpoint="http://localhost:9002",
        minio_bucket="wechat-assets",
    )

    service.prepare(
        html='<img src="http://localhost:9002/wechat-assets/generated-images/107/image.png">',
        cover_image_url="",
        tenant_id=107,
        article_id=23,
    )

    assert relay.stage_calls[0]["content_type"] == "image/jpeg"


def test_relay_publish_cleans_staged_images_when_request_fails(monkeypatch):
    """微信中转站请求失败时也必须释放已暂存的 COS 图片。"""
    from app.services import (
        wechat_gateway_policy,
        wechat_publisher,
        wechat_relay_client,
        wechat_relay_image_service,
    )

    cleanup_calls = []

    class FakeImageService:
        """返回已准备请求并记录 finally 清理。"""

        def prepare(self, **kwargs):
            return SimpleNamespace(
                html='<img src="https://cos.example.com/signed-body">',
                cover_image_url="https://cos.example.com/signed-cover",
                object_keys=["temporary/107/wechat-22/footer.png"],
            )

        def cleanup(self, object_keys):
            cleanup_calls.append(list(object_keys))

    class FailingRelayClient:
        """模拟中转站在已读取图片后返回失败。"""

        def __init__(self, **kwargs):
            pass

        def publish_article(self, **kwargs):
            raise RuntimeError("relay unavailable")

    monkeypatch.setattr(wechat_gateway_policy, "require_relay_publish_config", lambda: None)
    monkeypatch.setattr(wechat_relay_image_service, "WeChatRelayImageService", FakeImageService)
    monkeypatch.setattr(wechat_relay_client, "WeChatRelayClient", FailingRelayClient)
    monkeypatch.setattr(
        wechat_publisher,
        "_get_publisher_for_account",
        lambda *args, **kwargs: SimpleNamespace(
            app_id="wx-test",
            app_secret="secret",
            _format_content=lambda content: content,
        ),
    )
    article = SimpleNamespace(
        id=22,
        tenant_id=107,
        full_content='<img src="https://example.com/body.png">',
        content="",
        sub_title="",
        topic="家具",
        main_title="家具文章",
        cover_image="https://example.com/cover.png",
    )

    with pytest.raises(RuntimeError, match="relay unavailable"):
        wechat_publisher._publish_article_via_relay(
            db=SimpleNamespace(),
            article=article,
            account_id=103,
            mode="draft",
            tenant_id=107,
            actor_id=9,
        )

    assert cleanup_calls == [["temporary/107/wechat-22/footer.png"]]


def test_relay_request_id_changes_with_prepared_request_body():
    """COS 签名地址变化后请求体不同，requestId 也必须变化以满足中转站约束。"""
    from app.services.wechat_publisher import _build_relay_publish_request_id

    first = _build_relay_publish_request_id(
        tenant_id=107,
        account_id=103,
        article_id=22,
        mode="draft",
        html='<img src="https://cos.example.com/a?signature=1">',
        cover_image_url="https://cos.example.com/a?signature=1",
    )
    same = _build_relay_publish_request_id(
        tenant_id=107,
        account_id=103,
        article_id=22,
        mode="draft",
        html='<img src="https://cos.example.com/a?signature=1">',
        cover_image_url="https://cos.example.com/a?signature=1",
    )
    changed = _build_relay_publish_request_id(
        tenant_id=107,
        account_id=103,
        article_id=22,
        mode="draft",
        html='<img src="https://cos.example.com/a?signature=2">',
        cover_image_url="https://cos.example.com/a?signature=2",
    )

    assert first == same
    assert first != changed
    assert first.startswith("article-107-103-22-draft-")
