"""微信中转站客户端。

该模块只负责和固定 IP 中转站通信，不承载文章生成、账号归属校验、
数据库写入等业务职责。这样设计的原因是：后续中转站如果继续扩展评论、
客服消息、数据统计等能力，业务层可以继续复用同一套签名和调用规范，
避免每个服务里重复拼 HMAC Header。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Callable, Optional

import requests

from app.services.publish_domain_policy import map_relay_publish_mode


class WeChatRelayClient:
    """固定 IP 微信中转站 HTTP 客户端。

    职责边界：
    - 负责生成中转站 HMAC 鉴权 Header；
    - 负责按中转站协议组装 JSON 请求体；
    - 负责把中转站响应归一化为当前系统已有的 `media_id/publish_id` 形态。

    不在这里读取数据库或解密公众号密钥，是为了让该类可独立测试，也避免
    HTTP 协议层和业务身份校验耦合在一起。
    """

    PUBLISH_PATH = "/api/wechat/articles/publish"

    def __init__(
        self,
        base_url: str,
        relay_app_id: str,
        relay_secret: str,
        session: Optional[requests.Session] = None,
        nonce_factory: Optional[Callable[[], str]] = None,
        timestamp_factory: Optional[Callable[[], str]] = None,
        timeout: int = 180,
    ):
        """初始化中转站客户端。

        Args:
            base_url: 中转站根地址，例如 `http://8.166.141.59:21111`。
            relay_app_id: 中转站分配的调用方 ID。
            relay_secret: 中转站 HMAC 密钥，和微信公众号 appSecret 不是同一套密钥。
            session: 可注入 requests Session，便于单元测试捕获请求。
            nonce_factory: 可注入随机串生成器，测试时固定 nonce。
            timestamp_factory: 可注入时间戳生成器，测试时固定 timestamp。
            timeout: HTTP 超时时间。中转站会顺序下载正文图片、上传微信素材并创建
                草稿，多图文章通常超过 30 秒；默认设为 180 秒以覆盖该完整流程。
        """
        self.base_url = base_url.rstrip("/")
        self.relay_app_id = relay_app_id
        self.relay_secret = relay_secret
        self.session = session or requests.Session()
        self.session.trust_env = False  # 绕过系统代理，直连中转站
        self.session.proxies = {"http": None, "https": None}  # 显式禁用代理
        self.nonce_factory = nonce_factory or (lambda: f"nonce-{uuid.uuid4()}")
        self.timestamp_factory = timestamp_factory or (lambda: str(int(time.time())))
        self.timeout = timeout

    def publish_article(
        self,
        *,
        app_id: str,
        app_secret: str,
        request_id: str,
        tenant_id: Optional[str],
        publish_mode: str,
        publish_domain: str = "public",
        confirm_publish: bool,
        title: str,
        digest: str,
        html: str,
        cover_image_url: str,
        author: str = "",
        content_source_url: Optional[str] = None,
        need_open_comment: int = 0,
        only_fans_can_comment: int = 0,
    ) -> dict:
        """通过中转站创建微信草稿或提交发布。

        `publish_mode` 使用当前系统已有语义：`draft` 表示保存草稿，
        `direct` 表示真实发布；`publish_domain` 再决定真实发布走公域发布还是
        私域粉丝群发。这里统一映射到中转站协议，避免上层业务代码感知两套枚举。
        """
        relay_publish_mode = self._map_publish_mode(
            publish_mode,
            confirm_publish,
            publish_domain,
        )
        body = {
            "wechatCredential": {
                "appId": app_id,
                "appSecret": app_secret,
            },
            "publishPayload": {
                "requestId": request_id,
                "tenantId": tenant_id,
                "publishMode": relay_publish_mode,
                "confirmPublish": confirm_publish,
                "title": title,
                "digest": digest,
                "html": html,
                "author": author,
                "coverImageUrl": cover_image_url,
                # 协议要求缺失的来源链接传 null，空字符串会被远端 URL 校验拒绝。
                "contentSourceUrl": content_source_url or None,
                "needOpenComment": need_open_comment,
                "onlyFansCanComment": only_fans_can_comment,
            },
        }
        raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = self._post_json(self.PUBLISH_PATH, raw_body)
        return self._normalize_publish_result(result)

    def _map_publish_mode(
        self,
        publish_mode: str,
        confirm_publish: bool,
        publish_domain: str = "public",
    ) -> str:
        """把本系统发布枚举转换为中转站发布枚举。

        direct 发布属于真实微信副作用，必须显式确认；这里在客户端侧提前拦截，
        让错误发生在调用微信之前，减少误发风险。
        """
        return map_relay_publish_mode(
            publish_mode,
            publish_domain,
            confirm_publish,
        )

    def _post_json(self, path_with_query: str, raw_body: bytes) -> dict:
        """发送已序列化 JSON。

        签名必须基于实际发送的 raw bytes，不能先签一个对象再让 HTTP 库重新
        序列化，否则字段空格或中文转义差异都会导致中转站验签失败。
        """
        headers = self._build_headers("POST", path_with_query, raw_body)
        response = self.session.post(
            f"{self.base_url}{path_with_query}",
            data=raw_body,
            headers=headers,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            # 中转站 422 会包含字段路径。只记录响应摘要，绝不记录请求体中的密钥。
            raise RuntimeError(
                f"WeChat relay HTTP {response.status_code}: "
                f"{self._read_error_detail(response)}"
            ) from exc
        return response.json()

    @staticmethod
    def _read_error_detail(response: requests.Response) -> str:
        """读取受长度限制的中转站错误信息，便于定位协议字段问题。"""
        try:
            detail = json.dumps(response.json(), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            detail = response.text or "empty response body"
        return detail[:2000]

    def _build_headers(self, method: str, path_with_query: str, raw_body: bytes) -> dict:
        """构建中转站 HMAC 鉴权 Header。"""
        timestamp = self.timestamp_factory()
        nonce = self.nonce_factory()
        body_hash = hashlib.sha256(raw_body).hexdigest()
        signature_payload = "\n".join([
            method.upper(),
            path_with_query,
            timestamp,
            nonce,
            body_hash,
        ])
        signature = hmac.new(
            self.relay_secret.encode("utf-8"),
            signature_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Relay-App-Id": self.relay_app_id,
            "X-Relay-Timestamp": timestamp,
            "X-Relay-Nonce": nonce,
            "X-Relay-Signature": signature,
        }

    def _normalize_publish_result(self, result: dict) -> dict:
        """把中转站响应转换成当前系统已有发布结果结构。

        当前文章发布链路已经依赖 `media_id` 和 `publish_id` 字段。中转站统一
        返回 `wechatArticleId`，所以这里按业务状态拆回草稿 ID 或发布任务 ID，
        让上层调用点无需大面积重构。
        """
        if not result.get("success"):
            status = result.get("status", "FAILED")
            message = result.get("message", "微信中转站发布失败")
            raise RuntimeError(f"WeChat relay publish failed ({status}): {message}")

        status = result.get("status", "")
        wechat_article_id = result.get("wechatArticleId", "")
        normalized = {
            "relay_status": status,
            "wechat_article_id": wechat_article_id,
            "wechat_url": result.get("wechatUrl", ""),
            "message": result.get("message", ""),
            "raw": result,
        }
        if status == "DRAFT_CREATED":
            normalized["media_id"] = wechat_article_id
        elif status == "PUBLIC_PUBLISH_SUBMITTED":
            normalized["publish_id"] = wechat_article_id
            normalized["draft_saved"] = True
        elif status == "FOLLOWER_PUSH_SENT":
            normalized["msg_id"] = wechat_article_id
        return normalized
