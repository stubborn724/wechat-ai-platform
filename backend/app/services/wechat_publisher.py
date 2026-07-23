"""
微信公众号 API 发布服务 — 直接移植自 WeChat-AI-Auto-Publisher
"""

import json
import logging
import os
import re
import socket
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy.orm import Session

from app.models.mysql_models import Article, AccountCredential, WeChatAccount

logger = logging.getLogger(__name__)


class _IPv4Adapter(HTTPAdapter):
    """强制使用 IPv4 的 HTTP 适配器，解决 IPv6 导致微信 IP 白名单不匹配的问题"""

    def init_poolmanager(self, *args, **kwargs):
        kwargs['source_address'] = ('0.0.0.0', 0)  # 强制 IPv4
        return super().init_poolmanager(*args, **kwargs)

    def send(self, *args, **kwargs):
        # 保存原始 getaddrinfo
        original_getaddrinfo = socket.getaddrinfo

        def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

        socket.getaddrinfo = _ipv4_getaddrinfo
        try:
            return super().send(*args, **kwargs)
        finally:
            socket.getaddrinfo = original_getaddrinfo


class WechatPublisher:
    def __init__(self, app_id: str, app_secret: str, proxy_url: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.proxy_url = proxy_url
        self.access_token: Optional[str] = None
        self.access_token_expire_time: float = 0
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        # 创建使用 IPv4 的 session
        self._session = requests.Session()
        self._session.mount('https://api.weixin.qq.com', _IPv4Adapter())
        self._session.mount('https://mp.weixin.qq.com', _IPv4Adapter())
        logger.info("WeChatPublisher 初始化: app_id=%s...", app_id[:6] if app_id else "")

    def _make_request(self, method, url, **kwargs):
        """统一请求方法，自动使用 IPv4 session，添加代理和重试逻辑"""
        if self.proxies:
            kwargs['proxies'] = self.proxies
        if 'timeout' not in kwargs:
            kwargs['timeout'] = 30

        max_retries = 3
        retry_delay = 2
        for attempt in range(max_retries):
            try:
                if method.upper() == 'GET':
                    response = self._session.get(url, **kwargs)
                elif method.upper() == 'POST':
                    response = self._session.post(url, **kwargs)
                else:
                    raise ValueError(f"不支持的HTTP方法: {method}")
                response.raise_for_status()
                return response
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.warning("请求失败 (尝试 %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    logger.info("%d秒后重试...", delay)
                    time.sleep(delay)
                else:
                    logger.error("所有重试都失败了")
                    raise
            except Exception as e:
                logger.error("请求异常 (尝试 %d/%d): %s", attempt + 1, max_retries, e)
                raise

    def get_access_token(self):
        """获取并缓存 access_token（与原项目一致）"""
        current_time = time.time()
        if self.access_token and (current_time + 200) < self.access_token_expire_time:
            logger.info("使用缓存的access_token")
            return self.access_token

        url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={self.app_id}&secret={self.app_secret}"
        response = self._make_request('GET', url)
        data = response.json()

        if "access_token" in data and "expires_in" in data:
            self.access_token = data["access_token"]
            self.access_token_expire_time = current_time + data["expires_in"]
            logger.info("✅ 获取 access_token 成功，有效期至: %s",
                        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.access_token_expire_time)))
            return self.access_token
        else:
            if data.get("errcode") == 40164:
                logger.error("❌ IP白名单错误: %s\n💡 请登录 mp.weixin.qq.com → 开发 → 基本配置 → IP白名单", data.get('errmsg'))
            logger.error("获取访问令牌失败: %s", data)
            raise Exception(f"获取访问令牌失败: {data}")

    @staticmethod
    def _convert_inline_md(text: str) -> str:
        """Convert markdown inline elements to HTML (images, links, bold, italic)."""
        # Images: ![alt](url)  →  <img>
        text = re.sub(
            r'!\[([^\]]*)\]\(([^)]+)\)',
            r'<img src="\2" alt="\1" style="width:100%;border-radius:4px;margin:8px 0;" />',
            text,
        )
        # Links: [text](url)  →  <a>
        text = re.sub(
            r'\[([^\]]+)\]\(([^)]+)\)',
            r'<a href="\2">\1</a>',
            text,
        )
        # Bold: **text** or __text__
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
        # Italic: *text* or _text_
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
        return text

    def _format_content(self, content):
        """格式化微信公众号文章内容 — 支持 Markdown 图片、链接、加粗等"""
        content = (content or "").strip()
        if not content:
            return ""

        # Pre-process inline markdown before block-level parsing
        content = self._convert_inline_md(content)

        blocks = re.split(r"\n\s*\n", content)
        rendered_blocks = []

        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue

            if len(lines) == 1 and lines[0].startswith("### "):
                rendered_blocks.append(f"<h4>{lines[0][4:]}</h4>")
                continue

            if len(lines) == 1 and lines[0].startswith("## "):
                rendered_blocks.append(f"<h3>{lines[0][3:]}</h3>")
                continue

            if len(lines) == 1 and lines[0].startswith("# "):
                rendered_blocks.append(f"<h2>{lines[0][2:]}</h2>")
                continue

            if all(line.startswith("- ") for line in lines):
                items = "".join(f"<li>{line[2:]}</li>" for line in lines)
                rendered_blocks.append(f"<ul>{items}</ul>")
                continue

            if all(re.match(r"^\d+\.\s+", line) for line in lines):
                items = []
                for line in lines:
                    item_text = re.sub(r"^\d+\.\s+", "", line)
                    items.append(f"<li>{item_text}</li>")
                items = "".join(items)
                rendered_blocks.append(f"<ol>{items}</ol>")
                continue

            if all(line.startswith("> ") for line in lines):
                quote_html = "<br/>".join(line[2:] for line in lines)
                rendered_blocks.append(f"<blockquote>{quote_html}</blockquote>")
                continue

            paragraph_html = "<br/>".join(lines)
            rendered_blocks.append(f"<p>{paragraph_html}</p>")

        return "".join(rendered_blocks)

    def _upload_cover(self, image_path):
        """上传封面图片到微信，返回 media_id（与原项目 _upload_image is_cover=True 一致）"""
        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={self.access_token}&type=image"
        _, ext = os.path.splitext(image_path)
        filename = f"cover{ext or '.jpg'}"
        with open(image_path, "rb") as f:
            files = {"media": (filename, f, "image/jpeg")}
            data = {"description": json.dumps({"title": "封面图片"})}
            response = self._make_request('POST', url, data=data, files=files)
        result = response.json()
        logger.info("封面上传返回: %s", json.dumps(result, ensure_ascii=False))
        if result.get("media_id"):
            return result["media_id"]
        raise Exception(f"封面上传失败: {result}")

    def _upload_content_image(self, image_url):
        """将外部图片上传到微信 CDN，返回微信域名的 URL"""
        import hashlib
        import tempfile

        # 如果是微信自己的 URL，直接返回
        if "mmbiz.qpic.cn" in image_url or "mmbiz.qlogo.cn" in image_url:
            return image_url

        cache_key = hashlib.md5(image_url.encode()).hexdigest()
        cache_dir = os.path.join(tempfile.gettempdir(), "wechat_img_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{cache_key}.txt")

        # 检查缓存
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f:
                cached_url = f.read().strip()
            if cached_url:
                logger.info("使用缓存的微信图片 URL: %s", cached_url[:60])
                return cached_url

        logger.info("上传图片到微信 CDN: %s", image_url[:80])
        try:
            # 下载外部图片 — 优先用 requests，失败则尝试 MinIO SDK
            img_data = None
            content_type = "image/jpeg"
            try:
                img_resp = requests.get(image_url, timeout=15)
                img_resp.raise_for_status()
                img_data = img_resp.content
                content_type = img_resp.headers.get("Content-Type", "image/jpeg")
            except Exception as download_err:
                logger.warning("HTTP 下载图片失败，尝试 MinIO 读取: %s", download_err)
                # 如果 URL 是 MinIO 地址，直接用 SDK 读
                if "minio" in image_url.lower() or "localhost:900" in image_url:
                    try:
                        from app.services.storage_service import storage_service
                        # 解析 object_name: http://host:port/bucket/key -> key
                        path_parts = image_url.split("/")
                        if len(path_parts) >= 4:
                            obj_key = "/".join(path_parts[4:])  # skip http://host:port/bucket/
                            img_data = storage_service.download_bytes(obj_key)
                            ext = obj_key.split(".")[-1].lower() if "." in obj_key else "jpg"
                            content_type = {
                                "png": "image/png", "jpg": "image/jpeg",
                                "jpeg": "image/jpeg", "gif": "image/gif",
                                "webp": "image/webp",
                            }.get(ext, "image/jpeg")
                            logger.info("MinIO SDK 读取成功: %s (%d bytes)", obj_key, len(img_data))
                    except Exception as minio_err:
                        logger.warning("MinIO SDK 读取也失败: %s", minio_err)

            if not img_data:
                logger.warning("无法下载图片，保留原始 URL: %s", image_url[:60])
                return image_url

            content_type = content_type or "image/jpeg"
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"

            temp_path = os.path.join(tempfile.gettempdir(), f"wechat_img_{cache_key}{ext}")
            with open(temp_path, "wb") as f:
                f.write(img_data)

            # 上传到微信
            url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={self.access_token}"
            with open(temp_path, "rb") as f:
                files = {"media": (f"image{ext}", f, content_type)}
                response = self._make_request('POST', url, files=files)
            result = response.json()
            print(f"  [微信图片上传] 返回: {json.dumps(result, ensure_ascii=False)}")
            logger.info("微信图片上传返回: %s", json.dumps(result, ensure_ascii=False))
            if not result.get("url"):
                print(f"  ⚠️ 微信CDN上传失败: {result}")

            # 清理临时文件
            if os.path.exists(temp_path):
                os.remove(temp_path)

            if result.get("url"):
                wechat_url = result["url"]
                # 写入缓存
                try:
                    with open(cache_file, "w") as f:
                        f.write(wechat_url)
                except Exception:
                    pass
                return wechat_url

            logger.warning("图片上传失败，保留原始 URL: %s", result)
            return image_url
        except Exception as e:
            logger.warning("图片上传异常，保留原始 URL: %s", e)
            return image_url

    def _resolve_cover(self, cover_image_url):
        """下载封面图并上传到微信，返回 media_id；无 URL 则生成随机纯色封面。"""
        thumb_media_id = None
        temp_cover = None
        try:
            import tempfile as _tf
            import random
            cover_dir = _tf.gettempdir()

            if cover_image_url:
                # 尝试下载自定义封面
                try:
                    img_resp = requests.get(cover_image_url, timeout=15)
                    img_resp.raise_for_status()
                    ext = ".jpg"
                    ct = img_resp.headers.get("Content-Type", "")
                    if "png" in ct:
                        ext = ".png"
                    elif "webp" in ct:
                        ext = ".webp"
                    temp_cover = os.path.join(
                        cover_dir,
                        f"wechat_cover_{int(time.time())}_{random.randint(100,999)}{ext}",
                    )
                    with open(temp_cover, "wb") as f:
                        f.write(img_resp.content)
                    thumb_media_id = self._upload_cover(temp_cover)
                    logger.info("✅ 自定义封面上传成功，media_id: %s", thumb_media_id)
                    return thumb_media_id
                except Exception as e:
                    logger.warning("自定义封面上传失败，回退到随机封面: %s", e)

            # 回退：生成随机纯色封面
            from PIL import Image
            temp_cover = os.path.join(
                cover_dir,
                f"wechat_cover_{int(time.time())}_{random.randint(100,999)}.jpg",
            )
            color = (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200))
            img = Image.new('RGB', (900, 383), color=color)
            img.save(temp_cover, 'JPEG', quality=85)
            thumb_media_id = self._upload_cover(temp_cover)
            logger.info("✅ 随机封面上传成功，media_id: %s", thumb_media_id)
        except Exception as e:
            logger.warning("封面上传失败（将尝试不带封面提交）: %s", e)
        finally:
            if temp_cover and os.path.exists(temp_cover):
                os.remove(temp_cover)
        return thumb_media_id

    def save_draft(self, title, content_markdown, author="", summary="",
                   cover_image_url=None):
        """保存草稿到微信公众号（与原项目 publish_article(draft=True) 一致）

        Args:
            cover_image_url: 自定义封面图 URL，不为空则下载并用作封面。
        """
        MAX_TITLE_LENGTH = 64
        MAX_AUTHOR_LENGTH = 20
        MAX_DIGEST_LENGTH = 120

        if not self.access_token:
            self.get_access_token()

        # 格式化内容和字段
        title = title.strip()
        title = re.sub(r'\s+', ' ', title)[:MAX_TITLE_LENGTH]

        author = (author or "AI 运营平台").strip()
        author = re.sub(r'\s+', ' ', author)[:MAX_AUTHOR_LENGTH]

        digest = (summary or "").strip()
        digest = re.sub(r'[\n\t\s]+', ' ', digest)[:MAX_DIGEST_LENGTH]

        content = self._format_content(content_markdown)

        # 将外部图片上传到微信 CDN，避免草稿箱无法显示
        def _upload_all_images(html_content):
            def _replace_img_src(match):
                img_tag = match.group(0)
                src_match = re.search(r'src\s*=\s*["\'](https?://[^"\']+)["\']', img_tag)
                if src_match:
                    original_url = src_match.group(1)
                    wechat_url = self._upload_content_image(original_url)
                    if wechat_url != original_url:
                        return img_tag.replace(original_url, wechat_url)
                return img_tag
            return re.sub(r'<img[^>]+>', _replace_img_src, html_content)

        content = _upload_all_images(content)

        logger.info("标题: %s (长度:%d)", title, len(title))
        logger.info("作者: %s (长度:%d)", author, len(author))
        logger.info("摘要: %s (长度:%d)", digest, len(digest))
        logger.info("内容长度: %d 字符", len(content))
        print(f"  [微信草稿] HTML内容预览(前200字): {content[:200]}")
        if "二维码" in content or "qrcode" in content.lower() or "mmbiz.qpic.cn" in content:
            print(f"  [微信草稿] 检测到图片内容")
        else:
            print(f"  ⚠️ [微信草稿] 内容中未检测到图片!")

        # 上传封面图
        thumb_media_id = self._resolve_cover(cover_image_url)

        article_data = {
            "title": title,
            "author": author,
            "digest": digest,
            "content": content,
            "content_source_url": "",
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }
        if thumb_media_id:
            article_data["thumb_media_id"] = thumb_media_id

        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={self.access_token}"
        request_data = {"articles": [article_data]}
        json_data = json.dumps(request_data, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        logger.info("请求数据大小: %d bytes（含 thumb_media_id=%s）", len(json_data), bool(thumb_media_id))

        response = self._make_request('POST', url, data=json_data, headers=headers)
        result = response.json()
        logger.info("微信 API 返回: %s", json.dumps(result, ensure_ascii=False))

        if "errcode" in result and result["errcode"] != 0:
            errcode = result["errcode"]
            raise Exception(f"保存草稿失败(errcode={errcode}): {result}")

        logger.info("✅ 保存草稿成功: %s", result)
        return result

    # -----------------------------------------------------------------------
    # 直接发布（保存草稿 → 立即发布）
    # -----------------------------------------------------------------------

    def publish_directly(self, title, content_markdown, author="", summary="",
                         cover_image_url=None) -> dict:
        """保存草稿后立即提交发布

        Returns:
            dict with publish_id from WeChat API
        """
        # Step 1: 先保存草稿，拿到 media_id
        draft_result = self.save_draft(
            title=title,
            content_markdown=content_markdown,
            author=author,
            summary=summary,
            cover_image_url=cover_image_url,
        )
        media_id = draft_result.get("media_id")
        if not media_id:
            raise Exception(f"保存草稿成功但未获取到 media_id: {draft_result}")

        # Step 2: 提交发布
        if not self.access_token:
            self.get_access_token()

        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={self.access_token}"
        body = {"media_id": media_id}
        headers = {"Content-Type": "application/json"}
        response = self._make_request("POST", url, data=json.dumps(body), headers=headers)
        result = response.json()

        if "errcode" in result and result["errcode"] != 0:
            errcode = result["errcode"]
            logger.error("直接发布失败(errcode=%d): %s", errcode, result)
            # 即使发布失败，草稿已保存到草稿箱，返回 partial 状态
            return {
                "media_id": media_id,
                "publish_id": None,
                "draft_saved": True,
                "publish_error": str(result),
            }

        publish_id = result.get("publish_id")
        logger.info("✅ 直接发布成功! publish_id=%s, media_id=%s", publish_id, media_id)
        return {"media_id": media_id, "publish_id": publish_id, "draft_saved": True}


def _get_publisher_for_account(db: Session, account_id: int) -> WechatPublisher:
    """从数据库读取凭证，构造 WechatPublisher 实例"""
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id, WeChatAccount.deleted_at.is_(None)
    ).first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    credential = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id
    ).first()
    if not credential:
        raise ValueError(f"Credential for account {account_id} not found")

    logger.info("使用公众号: %s (app_id=%s...)", account.name, account.app_id[:6] if account.app_id else "")
    return WechatPublisher(app_id=account.app_id, app_secret=credential.encrypted_secret)


def publish_article(db: Session, article: Article, account_id: int,
                    mode: str = "draft") -> dict:
    """发布文章到微信公众号

    Args:
        db: 数据库 Session
        article: 文章对象
        account_id: 公众号 ID
        mode: 发布模式 — "draft" 保存草稿箱, "direct" 直接发布

    Returns:
        包含发布结果的 dict
    """
    publisher = _get_publisher_for_account(db, account_id)
    content = article.full_content or article.content or ""
    summary = article.sub_title or article.topic or ""
    title = article.main_title or article.topic or "无标题"

    if mode == "direct":
        return publisher.publish_directly(
            title=title,
            content_markdown=content,
            author="",
            summary=summary,
            cover_image_url=article.cover_image,
        )
    else:
        return publisher.save_draft(
            title=title,
            content_markdown=content,
            author="",
            summary=summary,
            cover_image_url=article.cover_image,
        )


def save_article_as_draft(db: Session, article: Article, account_id: int) -> dict:
    """保留的兼容函数 — 等价于 publish_article(..., mode='draft')"""
    return publish_article(db, article, account_id, mode="draft")
