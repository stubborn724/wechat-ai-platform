"""MinIO 图片地址解析工具。

同一个 MinIO 对象在不同运行环境中可能有多个访问入口：宿主机使用
``localhost:9002``，Docker 服务之间使用 ``minio:9000``，历史文章还可能保存
了旧的别名地址。业务模块不应该自行拼接或替换这些地址，否则质量检查、微信
发布和素材归档很容易出现不一致。

本模块只负责把可信的本地 MinIO URL 解析成对象键，不负责网络访问。调用方可以
使用内部 SDK 读取对象，也可以把对象键交给 COS 中转服务，避免把容器内部地址
暴露给外部系统。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final
from urllib.parse import unquote, urlsplit

from app.config import settings


_VALID_SCHEMES: Final = {"http", "https"}


class MinioUrlResolver:
    """在明确的 MinIO 入口集合中解析本地对象 URL。

    解析器采用白名单匹配 scheme、host、port 和桶路径，拒绝任意外部地址以及
    ``..`` 路径穿越。这样质量检查可以安全地把本地 URL 转为内部 SDK 读取，微信
    发布也能复用同一个对象键解析规则，不会扩大原有的 SSRF 访问边界。
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoints: Iterable[str],
    ) -> None:
        """根据桶名和所有可信访问入口建立不可变的解析前缀表。"""
        normalized_bucket = str(bucket or "").strip().strip("/")
        self._prefixes = self._build_prefixes(
            bucket=normalized_bucket,
            endpoints=endpoints,
        )
        if not self._prefixes:
            raise ValueError("至少需要一个有效的 MinIO 图片地址入口")

    @classmethod
    def from_settings(cls) -> "MinioUrlResolver":
        """从全局配置创建解析器，统一覆盖公共、内部和历史别名入口。"""
        use_ssl = bool(getattr(settings, "minio_use_ssl", False))
        internal_endpoint = cls.with_scheme(
            getattr(settings, "minio_endpoint", ""),
            use_ssl=use_ssl,
        )
        aliases = getattr(settings, "minio_url_aliases", "")
        alias_values = aliases.split(",") if isinstance(aliases, str) else aliases
        endpoints = (
            getattr(settings, "minio_public_endpoint", ""),
            internal_endpoint,
            *(str(value).strip() for value in alias_values if str(value).strip()),
        )
        return cls(
            bucket=getattr(settings, "minio_bucket", ""),
            endpoints=endpoints,
        )

    def extract_object_key(self, image_url: str) -> str | None:
        """从可信的本地 MinIO 图片 URL 提取对象键，外部 URL 返回 ``None``。"""
        parsed = urlsplit(str(image_url or "").strip())
        for scheme, netloc, object_path_prefix in self._prefixes:
            if (
                parsed.scheme.lower() != scheme
                or parsed.netloc.lower() != netloc
                or not parsed.path.startswith(object_path_prefix)
            ):
                continue

            object_key = unquote(
                parsed.path[len(object_path_prefix):]
            ).lstrip("/")
            if not object_key or ".." in object_key.split("/"):
                raise ValueError("本地素材 URL 包含无效对象键")
            return object_key
        return None

    @staticmethod
    def with_scheme(endpoint: str, *, use_ssl: bool) -> str:
        """为 Docker/MinIO 常见的 ``host:port`` 配置补齐访问协议。"""
        normalized = str(endpoint or "").strip()
        if not normalized or "://" in normalized:
            return normalized
        return f"{'https' if use_ssl else 'http'}://{normalized}"

    @classmethod
    def _build_prefixes(
        cls,
        *,
        bucket: str,
        endpoints: Iterable[str],
    ) -> tuple[tuple[str, str, str], ...]:
        """生成去重后的 ``(scheme, netloc, bucket_path_prefix)`` 前缀表。"""
        prefixes: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for endpoint in endpoints:
            parsed = urlsplit(str(endpoint or "").strip().rstrip("/"))
            if parsed.scheme.lower() not in _VALID_SCHEMES or not parsed.netloc:
                continue
            object_path_prefix = (
                f"{parsed.path.rstrip('/')}/{bucket}/"
                if parsed.path.rstrip("/")
                else f"/{bucket}/"
            )
            prefix = (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                object_path_prefix,
            )
            if prefix not in seen:
                seen.add(prefix)
                prefixes.append(prefix)
        return tuple(prefixes)
