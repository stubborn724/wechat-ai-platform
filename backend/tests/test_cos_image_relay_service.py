"""腾讯 COS 临时图片中转服务测试。

测试通过可注入的内存客户端隔离真实 COS 网络调用，重点验证中转服务自身负责的
配置校验、对象命名、上传参数、签名链接安全约束和精确清理行为。
"""

import sys
from types import SimpleNamespace

import pytest

from app.services.cos_image_relay_service import (
    CosImageRelayConfigurationError,
    CosImageRelayService,
)


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库清理夹具，使纯 COS 单元测试不访问业务数据库。

    本模块只验证内存中的参数转换和 SDK 调用边界，没有数据库状态需要准备；隔离该
    依赖也避免测试误删开发库数据，或因无关外键约束掩盖中转服务的真实结果。
    """
    yield


class FakeCosClient:
    """记录 COS SDK 调用的测试替身。

    该替身只复刻服务实际依赖的三个 SDK 方法，避免测试连接真实私有桶；记录完整
    关键字参数是为了验证服务生成的桶名、对象键和签名时效，而不是测试替身本身。
    """

    def __init__(self, signed_url: str = "https://private.example.com/signed-image") -> None:
        self.signed_url = signed_url
        self.put_calls = []
        self.presign_calls = []
        self.delete_calls = []

    def put_object(self, **kwargs):
        """记录上传参数并返回与 COS SDK 同形态的最小成功响应。"""
        self.put_calls.append(kwargs)
        return {"ETag": '"test-etag"'}

    def get_presigned_url(self, **kwargs):
        """记录签名参数并返回测试指定的 URL。"""
        self.presign_calls.append(kwargs)
        return self.signed_url

    def delete_object(self, **kwargs):
        """记录删除参数并返回与 COS SDK 同形态的最小成功响应。"""
        self.delete_calls.append(kwargs)
        return {}


def make_settings(**overrides):
    """构造一组完整配置，单个测试只覆盖与当前边界相关的字段。"""
    values = {
        "cos_enabled": True,
        "cos_secret_id": "test-secret-id",
        "cos_secret_key": "test-secret-key",
        "cos_region": "ap-guangzhou",
        "cos_bucket": "private-images-1250000000",
        "cos_endpoint": "",
        "cos_signed_url_expire_seconds": 600,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_configuration_lists_every_missing_field():
    """配置不完整时应一次指出全部缺失项，避免运维逐项试错。"""
    incomplete_settings = make_settings(
        cos_secret_id="",
        cos_secret_key="  ",
        cos_region=None,
        cos_bucket="",
    )

    with pytest.raises(CosImageRelayConfigurationError) as error:
        CosImageRelayService(client=FakeCosClient(), settings=incomplete_settings)

    message = str(error.value)
    assert "COS_SECRET_ID" in message
    assert "COS_SECRET_KEY" in message
    assert "COS_REGION" in message
    assert "COS_BUCKET" in message


def test_default_client_uses_global_settings_and_https_scheme(monkeypatch):
    """未注入客户端时应从全局配置创建官方 SDK 客户端，且固定使用 HTTPS。"""
    from app import config as app_config

    configured_values = vars(make_settings())
    for field_name, value in configured_values.items():
        monkeypatch.setattr(app_config.settings, field_name, value)

    created = {}

    class FakeCosConfig:
        """捕获官方配置构造参数，避免单测依赖真实云端凭证。"""

        def __init__(self, **kwargs) -> None:
            created["config_kwargs"] = kwargs

    class FakeSdkClient:
        """捕获服务传入的 SDK 配置对象。"""

        def __init__(self, config) -> None:
            created["client_config"] = config

    monkeypatch.setitem(
        sys.modules,
        "qcloud_cos",
        SimpleNamespace(CosConfig=FakeCosConfig, CosS3Client=FakeSdkClient),
    )

    service = CosImageRelayService()

    assert isinstance(service.client, FakeSdkClient)
    assert created["config_kwargs"] == {
        "Region": "ap-guangzhou",
        "SecretId": "test-secret-id",
        "SecretKey": "test-secret-key",
        "Scheme": "https",
    }


def test_stage_bytes_uploads_private_object_and_returns_https_signed_url():
    """暂存图片应使用隔离对象键上传，并返回限定时效的 HTTPS 签名地址。"""
    client = FakeCosClient(
        "https://private-images-1250000000.cos.ap-guangzhou.myqcloud.com/temporary/signed"
    )
    service = CosImageRelayService(client=client, settings=make_settings())

    result = service.stage_bytes(
        data=b"image-bytes",
        content_type="image/jpeg",
        tenant_id="tenant-42",
        run_id="run-99",
    )

    key_parts = result.object_key.split("/")
    assert key_parts[:3] == ["temporary", "tenant-42", "run-99"]
    assert len(key_parts) == 4
    assert key_parts[3].endswith(".jpg")
    assert len(key_parts[3].removesuffix(".jpg")) == 32
    assert result.signed_url.startswith("https://")
    assert client.put_calls == [{
        "Bucket": "private-images-1250000000",
        "Key": result.object_key,
        "Body": b"image-bytes",
        "ContentType": "image/jpeg",
    }]
    assert client.presign_calls == [{
        "Method": "GET",
        "Bucket": "private-images-1250000000",
        "Key": result.object_key,
        "Expired": 600,
    }]


def test_stage_bytes_uses_bin_extension_for_unknown_content_type():
    """未知 MIME 类型必须回退安全后缀，不能把外部输入直接拼进对象键。"""
    service = CosImageRelayService(client=FakeCosClient(), settings=make_settings())

    result = service.stage_bytes(
        data=b"unknown-binary",
        content_type="application/x-untrusted/../../png",
        tenant_id="tenant-1",
        run_id="run-1",
    )

    assert result.object_key.endswith(".bin")
    assert ".." not in result.object_key


def test_stage_bytes_rejects_empty_data_before_upload():
    """空内容没有可中转价值，应在访问 COS 前被明确拒绝。"""
    client = FakeCosClient()
    service = CosImageRelayService(client=client, settings=make_settings())

    with pytest.raises(ValueError, match="data"):
        service.stage_bytes(
            data=b"",
            content_type="image/png",
            tenant_id="tenant-1",
            run_id="run-1",
        )

    assert client.put_calls == []


def test_stage_bytes_rejects_non_https_signed_url():
    """私有图片凭证不得经明文 HTTP 传输，即使 SDK 返回地址也要二次校验。"""
    service = CosImageRelayService(
        client=FakeCosClient("http://private.example.com/insecure-signed-image"),
        settings=make_settings(),
    )

    with pytest.raises(CosImageRelayConfigurationError, match="HTTPS"):
        service.stage_bytes(
            data=b"image-bytes",
            content_type="image/png",
            tenant_id="tenant-1",
            run_id="run-1",
        )


def test_delete_object_deletes_exact_key_from_configured_bucket():
    """清理只允许删除调用方明确给出的对象键，不能扩大为前缀删除。"""
    client = FakeCosClient()
    service = CosImageRelayService(client=client, settings=make_settings())
    object_key = "temporary/tenant-42/run-99/0123456789abcdef0123456789abcdef.png"

    service.delete_object(object_key)

    assert client.delete_calls == [{
        "Bucket": "private-images-1250000000",
        "Key": object_key,
    }]
