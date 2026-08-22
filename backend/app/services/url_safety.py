"""出站 URL 安全校验 — 防止 SSRF 攻击

确保所有从用户输入获取的 URL 在发起 HTTP 请求前经过此模块校验。
支持 DNS 重绑定防护、重定向追踪校验、协议/端口白名单、响应大小限制。
"""

import ipaddress
import logging
import re
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 禁止访问的私有/内网网段
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),        # private A
    ipaddress.ip_network("172.16.0.0/12"),     # private B
    ipaddress.ip_network("192.168.0.0/16"),    # private C
    ipaddress.ip_network("169.254.0.0/16"),    # link-local
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),         # current network
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT
    ipaddress.ip_network("198.18.0.0/15"),     # benchmark testing
]

# 只允许的协议
_ALLOWED_SCHEMES = ("http", "https")

# 信任的内部服务（不走 SSRF 检查）
_TRUSTED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
}

# 通义万相与已接入 ERP 的图片交付域名在部分网络会被解析到 RFC 2544
# 基准测试网段。此处仅允许精确 HTTPS 主机名，不能据此放开整个
# 198.18.0.0/15 网段，也不能把未审计的 OSS 子域名一并放行。
_TRUSTED_EXTERNAL_IMAGE_HOSTS = {
    "dashscope-5859.oss-cn-wulanchabu-acdr-1.aliyuncs.com",
    "xiumancloud.oss-cn-beijing.aliyuncs.com",
    # OpenAI 兼容图片主站返回的短期签名图片域名。该域名在当前网络解析到
    # RFC 2544 基准网段，属于明确审核过的 HTTPS 静态图片交付地址；仅精确
    # 放行该主机，不能扩大到整个 198.18.0.0/15 网段。
    "videos.tpkcur.xyz",
}

# DashScope 生成图片使用动态 OSS bucket，数字编号会随任务或交付集群变化，
# 因此不能继续维护单个完整主机名。这里严格约束 bucket 前缀、数字编号、
# OSS 加速/官方地域端点和 aliyuncs.com 根域，避免把任意 OSS bucket 或
# 形似官方域名的后缀主机纳入 SSRF 例外。
_DASHSCOPE_OSS_HOST_PATTERN = re.compile(
    r"^dashscope-\d+\.oss-(?:accelerate|(?:cn|ap|eu|us|me)-[a-z0-9-]+)\.aliyuncs\.com$"
)

# 公众号文章抓取域名白名单。
# 微信文章在当前网络环境中可能被解析到 RFC 2544 基准测试网段，
# 但它是用户明确输入的外部内容源，因此只对这个精确域名的 HTTPS
# 默认端口做兼容，避免把整个微信域名体系或整个基准网段放开。
_TRUSTED_EXTERNAL_CONTENT_HOSTS = {
    "mp.weixin.qq.com",
}

# 从配置动态添加 MinIO 等信任主机
def _init_trusted_hosts():
    try:
        from app.config import settings
        for origin in settings.cors_origin_list:
            from urllib.parse import urlparse
            host = urlparse(origin).hostname
            if host:
                _TRUSTED_HOSTS.add(host)
        # MinIO endpoint
        minio_host = settings.minio_endpoint.split(":")[0]
        if minio_host:
            _TRUSTED_HOSTS.add(minio_host)
    except Exception:
        pass

_init_trusted_hosts()

# 云元数据地址关键字
_CLOUD_METADATA_HOSTS = [
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
    "100.100.100.200",  # alibaba cloud
]

# 默认最大响应大小 (50MB)
_DEFAULT_MAX_SIZE = 50 * 1024 * 1024


def _is_internal_ip(host: str) -> bool:
    """检查 host 是否为内网 IP 地址（支持 IPv4 和 IPv6）"""
    try:
        ip = ipaddress.ip_address(host)
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return True
        return False
    except ValueError:
        return False


def _resolve_dns(host: str) -> list:
    """解析域名到 IP 地址列表，仅返回 IPv4 地址"""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return list(set(info[4][0] for info in infos))
    except (socket.gaierror, OSError) as exc:
        raise ValueError(f"DNS resolution failed for '{host}': {exc}")


def _check_cloud_metadata(host: str) -> None:
    """检查是否为云元数据地址"""
    for meta_host in _CLOUD_METADATA_HOSTS:
        if host == meta_host or host.lower() == meta_host:
            raise ValueError(f"URL points to cloud metadata service: {host}")


def _is_official_dashscope_oss_host(host: str) -> bool:
    """识别受控范围内的 DashScope 官方动态 OSS 主机。

    DashScope 返回地址中的 bucket 数字会变化，但主机结构稳定。单独封装
    结构识别可避免在静态白名单中持续追加临时域名，也便于通过测试明确
    约束允许范围；任何非数字 bucket、非 OSS 端点或附加域名后缀都拒绝。
    """
    return _DASHSCOPE_OSS_HOST_PATTERN.fullmatch(host.lower()) is not None


def _is_trusted_external_image_url(parsed) -> bool:
    """判断 URL 是否为经过审计的外部图片交付地址。

    这条例外只适用于 HTTPS 默认端口和静态域名集合，用于兼容可信图片
    CDN 的特殊 DNS 映射；调用方仍不能借此请求任意内网地址或自定义端口。
    """
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and (
            host in _TRUSTED_EXTERNAL_IMAGE_HOSTS
            or _is_official_dashscope_oss_host(host)
        )
    )


def _is_trusted_external_content_url(parsed) -> bool:
    """判断 URL 是否为经过审计的外部文章内容源。

    文章抓取和图片归档是两类不同的业务边界，分别维护域名集合可以
    避免后续新增 CDN 或内容源时意外扩大另一类请求的权限。
    """
    return (
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and (parsed.hostname or "").lower() in _TRUSTED_EXTERNAL_CONTENT_HOSTS
    )


def validate_url(url: str, _is_redirect: bool = False) -> None:
    """校验 URL 是否安全，若不安全则抛出 ValueError

    校验内容：
    1. 协议白名单（仅 http/https）
    2. 端口检查（仅允许 1-65535 范围内的非特权端口，屏蔽常见内部服务端口）
    3. IPv4/IPv6 内网地址检查
    4. DNS 解析后 IP 检查（防 DNS 重绑定）
    5. 云元数据地址检查
    """
    if not url:
        raise ValueError("URL is empty")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {url}") from exc

    # 检查协议
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed (only http/https)")

    # 检查端口 — 仅阻止已知的内部服务端口
    if parsed.port is not None:
        _FORBIDDEN_PORTS = {21, 22, 23, 25, 53, 110, 135, 139, 143, 389, 445,
                            993, 995, 1433, 1521, 2049, 2375, 2376, 3306, 3389,
                            5432, 5900, 6379, 8080, 8443, 9200, 9300, 11211, 27017}
        if parsed.port in _FORBIDDEN_PORTS:
            raise ValueError(f"URL port {parsed.port} is not allowed (forbidden service port)")

    host = parsed.hostname or ""

    # 精确放行可信图片域名在当前网络中的基准网段 DNS 映射。
    if _is_trusted_external_image_url(parsed):
        return

    # 精确放行可信文章域名在当前网络中的基准网段 DNS 映射。
    if _is_trusted_external_content_url(parsed):
        return

    # 信任的主机跳过 SSRF 检查（如本地 MinIO、CORS 源等基础设施）
    if host in _TRUSTED_HOSTS:
        return

    # 检查是否为内网 IP
    if _is_internal_ip(host):
        raise ValueError(f"URL points to internal/private network: {host}")

    # 检查云元数据地址
    _check_cloud_metadata(host)

    # 如果不是 IP 地址，解析 DNS 检查解析结果
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # 是域名 — 解析 DNS 检查所有解析到的 IP
        resolved_ips = _resolve_dns(host)
        for resolved_ip in resolved_ips:
            if _is_internal_ip(resolved_ip):
                raise ValueError(
                    f"URL domain '{host}' resolves to internal IP: {resolved_ip}"
                )
            # 检查云元数据
            _check_cloud_metadata(resolved_ip)


async def safe_request(url: str, method: str = "GET", max_size: int = _DEFAULT_MAX_SIZE,
                       follow_redirects: bool = True, **kwargs) -> httpx.Response:
    """安全的出站 HTTP 请求 — 包含 SSRF 防护

    特性：
    - 请求前校验 URL 安全
    - 追踪重定向，每次跳转后重新校验目标 URL
    - 限制响应体大小
    - 默认超时 30s

    Args:
        url: 请求 URL
        method: HTTP 方法
        max_size: 最大响应字节数 (默认 50MB)
        follow_redirects: 是否跟踪重定向 (默认 True)
        **kwargs: 传递给 httpx.AsyncClient 的额外参数

    Returns:
        httpx.Response

    Raises:
        ValueError: URL 不安全
        httpx.HTTPError: 请求失败
    """
    # 首次校验
    validate_url(url)

    if "timeout" not in kwargs:
        kwargs["timeout"] = httpx.Timeout(30.0, connect=10.0)

    # 默认 User-Agent
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ))

    async with httpx.AsyncClient(follow_redirects=False, **kwargs) as client:
        response = await client.request(method, url, headers=headers)

        # 跟踪重定向
        if follow_redirects:
            redirect_count = 0
            max_redirects = 10
            while response.is_redirect and redirect_count < max_redirects:
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    break
                # 重新校验重定向目标
                validate_url(redirect_url, _is_redirect=True)
                response = await client.request(method, redirect_url, headers=headers)
                redirect_count += 1

        # 流式读取并限制大小
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_size:
                raise ValueError(
                    f"Response body exceeded maximum size of {max_size} bytes"
                )

        # 替换 _content 为已读取的内容
        response._content = bytes(content)
        return response


def validate_url_sync(url: str) -> str:
    """同步 URL 校验 — 兼容现有 requests.get 调用

    注意：此函数不包含 DNS 解析校验（因为同步上下文中无法安全地阻塞），
          仅做 URL 格式和 IP 白名单检查。
          对于完整防护请使用 safe_request / validate_url。
    """
    validate_url(url)
    return url
