"""微信中转站发布前的本地 MinIO 图片临时公网化服务。

服务只改写发往中转站的请求副本，不修改文章数据库内容。MinIO 保持长期归档，
COS 对象由调用方在发布请求结束后清理，避免产生两套长期素材来源。
"""

from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.services.cos_image_relay_service import CosImageRelayService
from app.services.storage_service import storage_service


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
    ) -> None:
        """注入存储依赖并固化本地公开 URL 的精确解析前缀。"""
        self.storage = storage
        self.relay = relay or CosImageRelayService()
        endpoint = (minio_public_endpoint or settings.minio_public_endpoint).rstrip("/")
        self._minio_base = urlsplit(endpoint)
        bucket = (minio_bucket or settings.minio_bucket).strip().strip("/")
        self._object_path_prefix = f"{self._minio_base.path.rstrip('/')}/{bucket}/"

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
        try:
            for image_url in dict.fromkeys(candidate_urls):
                storage_key = self._extract_local_storage_key(image_url)
                if not storage_key:
                    continue

                image_bytes = self.storage.download_bytes(storage_key)
                content_type = self._detect_image_content_type(image_bytes, storage_key)
                relay_object = self.relay.stage_bytes(
                    data=image_bytes,
                    content_type=content_type,
                    tenant_id=tenant_id,
                    run_id=f"wechat-{article_id}",
                )
                replacements[image_url] = relay_object.signed_url
                object_keys.append(relay_object.object_key)
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
        """仅从当前 MinIO 公共前缀解析对象键，并拒绝空键或路径穿越。"""
        parsed = urlsplit(str(image_url or "").strip())
        if (
            parsed.scheme.lower() != self._minio_base.scheme.lower()
            or parsed.netloc.lower() != self._minio_base.netloc.lower()
            or not parsed.path.startswith(self._object_path_prefix)
        ):
            return None

        object_key = unquote(parsed.path[len(self._object_path_prefix):]).lstrip("/")
        if not object_key or ".." in object_key.split("/"):
            raise ValueError("本地素材 URL 包含无效对象键")
        return object_key

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
