"""微信中转站发布前的本地 MinIO 图片临时公网化服务。

服务只改写发往中转站的请求副本，不修改文章数据库内容。MinIO 保持长期归档，
COS 对象由调用方在发布请求结束后清理，避免产生两套长期素材来源。
"""

from __future__ import annotations

import logging
import mimetypes
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from collections.abc import Iterable

from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.services.cos_image_relay_service import CosImageRelayService
from app.services.storage_service import storage_service
from app.services.minio_url_resolver import MinioUrlResolver


logger = logging.getLogger(__name__)
_HTML_IMAGE_URL_PATTERN = re.compile(
    r'<img[^>]+src\s*=\s*(["\'])(.*?)\1',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreparedWeChatRelayImages:
    """微信中转请求使用的 HTML、封面地址和待清理 COS 对象键。"""

    html: str
    cover_image_url: str
    object_keys: list[str]


class WeChatRelayImageService:
    """把本地 MinIO 图片临时转换为中转站可访问的 COS HTTPS 地址。"""

    def __init__(
        self,
        *,
        storage=storage_service,
        relay: CosImageRelayService | None = None,
        minio_public_endpoint: str | None = None,
        minio_bucket: str | None = None,
        minio_url_aliases: Iterable[str] | str | None = None,
    ) -> None:
        """注入依赖并建立所有已知 MinIO 入口到对象键的解析表。

        ``MINIO_PUBLIC_ENDPOINT`` 不是稳定的唯一入口：宿主机通常使用
        ``localhost:9002``，Docker 内部则使用 ``minio:9000``，历史文章还可能
        留下迁移前的旧域名。这里把入口差异收敛在发布适配层，后续流程只处理
        对象键和图片字节，从而既不修改数据库里的归档地址，也不会把本地地址
        泄漏给微信中转站。
        """
        self.storage = storage
        self.relay = relay or CosImageRelayService()
        bucket = (minio_bucket or settings.minio_bucket).strip().strip("/")
        configured_public_endpoint = (
            minio_public_endpoint or settings.minio_public_endpoint
        )
        configured_aliases = (
            minio_url_aliases
            if minio_url_aliases is not None
            else getattr(settings, "minio_url_aliases", "")
        )
        internal_endpoint = getattr(settings, "minio_endpoint", "")
        use_ssl = bool(getattr(settings, "minio_use_ssl", False))
        self._minio_url_resolver = MinioUrlResolver(
            bucket=bucket,
            endpoints=(
                configured_public_endpoint,
                MinioUrlResolver.with_scheme(internal_endpoint, use_ssl=use_ssl),
                *self._split_aliases(configured_aliases),
            ),
        )

    def prepare(
        self,
        *,
        html: str,
        cover_image_url: str,
        tenant_id: int,
        article_id: int,
    ) -> PreparedWeChatRelayImages:
        """去重中转正文和封面中的本地图片，并返回仅用于本次发布的请求副本。

        外部 HTTP URL 不属于本服务职责，保持原值交给发布校验拒绝；这样不会把
        任意网络地址误当作可信对象键，也不会掩盖部署配置问题。
        """
        candidate_urls = [match.group(2) for match in _HTML_IMAGE_URL_PATTERN.finditer(html or "")]
        if cover_image_url:
            candidate_urls.append(cover_image_url)

        replacements: dict[str, str] = {}
        object_keys: list[str] = []
        local_items = []
        for image_url in dict.fromkeys(candidate_urls):
            storage_key = self._extract_local_storage_key(image_url)
            if storage_key:
                local_items.append((image_url, storage_key))

        try:
            def _stage_local_image(image_url: str, storage_key: str):
                """下载并暂存单张本地图片，返回原 URL 与 COS 结果。

                微信发布前正文图只需要临时公网化，多个对象之间没有顺序依赖。使用
                线程池并发等待 MinIO 下载和 COS 上传，可以减少草稿发布前的尾部
                延迟；替换 HTML 时仍按 ``replacements`` 映射执行，不受完成顺序影响。
                """
                image_bytes = self.storage.download_bytes(storage_key)
                content_type = self._detect_image_content_type(image_bytes, storage_key)
                relay_object = self.relay.stage_bytes(
                    data=image_bytes,
                    content_type=content_type,
                    tenant_id=tenant_id,
                    run_id=f"wechat-{article_id}",
                )
                return image_url, relay_object

            if local_items:
                started_at = time.perf_counter()
                max_workers = min(4, len(local_items))
                logger.info(
                    "开始并发准备微信中转图片 article_id=%s count=%d concurrency=%d",
                    article_id,
                    len(local_items),
                    max_workers,
                )
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(_stage_local_image, image_url, storage_key)
                        for image_url, storage_key in local_items
                    ]
                    for future in as_completed(futures):
                        image_url, relay_object = future.result()
                        replacements[image_url] = relay_object.signed_url
                        object_keys.append(relay_object.object_key)
                logger.info(
                    "微信中转图片准备完成 article_id=%s count=%d elapsed=%.2fs",
                    article_id,
                    len(object_keys),
                    time.perf_counter() - started_at,
                )
        except Exception:
            # 准备阶段未返回对象键时调用方无法清理，因此这里回收部分成功对象。
            self.cleanup(object_keys)
            raise

        prepared_html = html or ""
        for source_url, signed_url in replacements.items():
            prepared_html = prepared_html.replace(source_url, signed_url)
        prepared_cover = replacements.get(cover_image_url, cover_image_url)
        return PreparedWeChatRelayImages(
            html=prepared_html,
            cover_image_url=prepared_cover,
            object_keys=object_keys,
        )

    def cleanup(self, object_keys: list[str]) -> None:
        """按精确对象键回收一次微信请求的 COS 临时图片，失败只记录告警。"""
        for object_key in reversed(object_keys):
            try:
                self.relay.delete_object(object_key)
            except Exception as exc:
                logger.warning("微信发布 COS 临时图片清理失败 key=%s: %s", object_key, exc)

    def _extract_local_storage_key(self, image_url: str) -> str | None:
        """从任一已配置 MinIO 入口解析对象键，并拒绝空键或路径穿越。

        只接受配置过的 scheme、host、port 和桶路径。即使正文中出现了其它
        外部 HTTP 地址，也不会被误当作本地对象去下载，保持原有 SSRF 边界。
        """
        return self._minio_url_resolver.extract_object_key(image_url)

    @staticmethod
    def _split_aliases(aliases: Iterable[str] | str) -> tuple[str, ...]:
        """把环境变量或测试注入的地址列表统一为去空白后的元组。"""
        if isinstance(aliases, str):
            values = aliases.split(",")
        else:
            values = aliases
        return tuple(str(value).strip() for value in values if str(value).strip())

    @staticmethod
    def _with_scheme(endpoint: str, *, use_ssl: bool) -> str:
        """为 MinIO SDK 的 ``host:port`` 入口补齐 URL scheme。"""
        normalized = str(endpoint or "").strip()
        if not normalized or "://" in normalized:
            return normalized
        return f"{'https' if use_ssl else 'http'}://{normalized}"

    @staticmethod
    def _detect_image_content_type(image_bytes: bytes, storage_key: str) -> str:
        """以真实图片字节为准识别 MIME 类型，避免上游错误命名导致微信拒绝。

        部分图像服务会把 JPEG 内容保存为 ``.png``。COS 的 ``Content-Type``、
        对象后缀与实际二进制必须保持一致，否则微信中转站下载后会校验失败。
        Pillow 识别失败时才回退存储对象后缀，以兼容历史素材和非标准图片格式。
        """
        format_to_mime = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
            "GIF": "image/gif",
            "BMP": "image/bmp",
            "TIFF": "image/tiff",
            "AVIF": "image/avif",
        }
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                detected_content_type = format_to_mime.get(str(image.format or "").upper())
                if detected_content_type:
                    return detected_content_type
        except (UnidentifiedImageError, OSError, ValueError):
            # 后缀回退仅用于无法识别的历史对象；正常生成图片必须走上方真实字节识别。
            logger.warning("无法从图片字节识别 MIME，回退存储后缀: %s", storage_key)

        return mimetypes.guess_type(storage_key)[0] or "application/octet-stream"
