"""腾讯 COS 私有图片临时中转服务。

本模块只负责将一次任务所需的图片字节暂存到私有 COS、生成短期 HTTPS 签名
地址，以及按精确对象键清理。MinIO 继续承担长期素材归档，二者职责不会混合。
"""

import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


class CosImageRelayConfigurationError(RuntimeError):
    """COS 中转配置不完整，或 SDK 生成了不满足安全约束的结果。"""


@dataclass(frozen=True)
class CosRelayObject:
    """一次暂存操作返回给业务层的最小引用。

    ``object_key`` 用于任务结束时精确清理；``signed_url`` 只供外部图像服务在
    有效期内读取私有对象，调用方不需要也不应接触 COS 凭证。
    """

    object_key: str
    signed_url: str


class CosImageRelayService:
    """将任务图片短暂中转到私有 COS，并生成带时效的 HTTPS 读取地址。

    客户端与配置均支持注入，使单元测试无需真实云端连接；生产环境不传参数时，
    服务会从 ``app.config.settings`` 读取配置并创建腾讯云官方 SDK 客户端。
    """

    # MIME 类型只能映射到固定白名单后缀，避免把不可信 Content-Type 拼入对象键。
    _CONTENT_TYPE_EXTENSIONS: Mapping[str, str] = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/bmp": "bmp",
        "image/tiff": "tif",
        "image/avif": "avif",
    }

    def __init__(self, *, settings: Any = None, client: Any = None) -> None:
        """校验中转配置，并按需创建 COS SDK 客户端。

        即使测试注入客户端也会执行配置校验，因为桶名、区域和签名时效仍决定
        服务行为；这样可避免测试路径和生产路径产生两套不一致的初始化规则。
        """
        if settings is None:
            from app.config import settings as application_settings

            settings = application_settings

        self._validate_settings(settings)
        self.bucket = str(settings.cos_bucket).strip()
        self.endpoint = (
            str(getattr(settings, "cos_endpoint", "") or "").strip().rstrip("/")
        )
        self.signed_url_expire_seconds = int(settings.cos_signed_url_expire_seconds)
        self.client = client if client is not None else self._create_sdk_client(settings)

    def stage_bytes(
        self,
        data: bytes,
        content_type: str,
        tenant_id: int,
        run_id: int,
    ) -> CosRelayObject:
        """上传非空字节并返回任务隔离的对象键和短期 HTTPS 签名 URL。

        对象键按租户和运行批次分层，便于审计和精确清理；随机 UUID 防止同一任务
        中的图片重名。未知 MIME 类型统一按二进制保存，不能影响对象键结构。
        """
        if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
            raise ValueError("data 必须是非空字节内容")

        normalized_content_type = self._normalize_content_type(content_type)
        extension = self._CONTENT_TYPE_EXTENSIONS.get(normalized_content_type, "bin")
        upload_content_type = (
            normalized_content_type
            if normalized_content_type in self._CONTENT_TYPE_EXTENSIONS
            else "application/octet-stream"
        )
        object_key = f"temporary/{tenant_id}/{run_id}/{uuid.uuid4().hex}.{extension}"

        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=bytes(data),
            ContentType=upload_content_type,
        )
        signed_url = str(
            self.client.get_presigned_url(
                Method="GET",
                Bucket=self.bucket,
                Key=object_key,
                Expired=self.signed_url_expire_seconds,
            )
        ).strip()

        # 签名 URL 会携带临时访问凭证，因此即使 SDK 返回异常地址也不能降级到 HTTP。
        parsed_url = urlsplit(signed_url)
        if parsed_url.scheme.lower() != "https" or not parsed_url.netloc:
            # 上传已经完成时主动回收对象，防止安全校验失败留下孤儿临时文件。
            self.delete_object(object_key)
            raise CosImageRelayConfigurationError("COS 签名 URL 必须是有效的 HTTPS 地址")

        return CosRelayObject(object_key=object_key, signed_url=signed_url)

    def delete_object(self, object_key: str) -> None:
        """从已配置桶中删除一个精确对象键，不执行前缀或批量删除。

        清理异常保持 SDK 原始异常向上抛出，由任务编排层决定记录告警还是终止流程；
        服务本身不吞掉失败，否则临时对象泄漏将无法被监控发现。
        """
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    @staticmethod
    def _validate_settings(settings: Any) -> None:
        """一次性报告所有缺失或无效字段，降低部署配置的排查成本。"""
        missing_fields = []
        if not bool(getattr(settings, "cos_enabled", False)):
            missing_fields.append("COS_ENABLED")

        required_text_fields = {
            "COS_SECRET_ID": "cos_secret_id",
            "COS_SECRET_KEY": "cos_secret_key",
            "COS_REGION": "cos_region",
            "COS_BUCKET": "cos_bucket",
        }
        for environment_name, attribute_name in required_text_fields.items():
            value = getattr(settings, attribute_name, "")
            if not str(value or "").strip():
                missing_fields.append(environment_name)

        try:
            expire_seconds = int(getattr(settings, "cos_signed_url_expire_seconds", 0))
        except (TypeError, ValueError):
            expire_seconds = 0
        if expire_seconds <= 0:
            missing_fields.append("COS_SIGNED_URL_EXPIRE_SECONDS")

        if missing_fields:
            raise CosImageRelayConfigurationError(
                f"COS 图片中转缺少或包含无效配置：{', '.join(missing_fields)}"
            )

    @staticmethod
    def _create_sdk_client(settings: Any) -> Any:
        """使用已校验的全局配置创建腾讯云官方 COS 客户端。

        SDK 延迟导入使注入测试客户端的路径不依赖本机安装状态；生产路径缺少依赖
        时转为可理解的配置异常，而不是暴露模糊的模块导入堆栈。
        """
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as exc:
            raise CosImageRelayConfigurationError(
                "未安装腾讯 COS SDK：cos-python-sdk-v5"
            ) from exc

        sdk_config = CosConfig(
            Region=str(settings.cos_region).strip(),
            SecretId=str(settings.cos_secret_id).strip(),
            SecretKey=str(settings.cos_secret_key).strip(),
            Scheme="https",
        )
        return CosS3Client(sdk_config)

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        """规范化 MIME 类型并去除参数，仅保留后缀白名单匹配所需的主类型。"""
        return str(content_type or "").split(";", maxsplit=1)[0].strip().lower()
