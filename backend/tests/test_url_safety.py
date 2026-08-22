"""出站 URL 安全校验的回归测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """URL 纯函数测试不依赖数据库，屏蔽全局清理夹具的外键副作用。"""
    yield


def test_allows_configured_dashscope_image_host_with_benchmark_dns(monkeypatch):
    """通义万相官方 HTTPS 图片域名在特定网络映射到基准网段时仍可归档。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.1.118"])

    url_safety.validate_url(
        "https://dashscope-5859.oss-cn-wulanchabu-acdr-1.aliyuncs.com/image.png"
    )


@pytest.mark.parametrize(
    "host",
    [
        "dashscope-0484.oss-accelerate.aliyuncs.com",
        "dashscope-9371.oss-cn-hangzhou.aliyuncs.com",
    ],
)
def test_allows_official_dynamic_dashscope_image_hosts_with_benchmark_dns(
    monkeypatch,
    host,
):
    """DashScope 动态 bucket 变化时，受控的官方 HTTPS OSS 地址仍可归档。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda resolved_host: ["198.18.2.188"])

    url_safety.validate_url(f"https://{host}/generated/image.png")


@pytest.mark.parametrize(
    "url",
    [
        "https://dashscope-0484.oss-accelerate.aliyuncs.com.evil.test/image.png",
        "https://customer-bucket.oss-accelerate.aliyuncs.com/image.png",
        "https://dashscope-demo.oss-accelerate.aliyuncs.com/image.png",
        "https://dashscope-0484.cdn.aliyuncs.com/image.png",
        "http://dashscope-0484.oss-accelerate.aliyuncs.com/image.png",
        "https://dashscope-0484.oss-accelerate.aliyuncs.com:9443/image.png",
    ],
)
def test_rejects_untrusted_dashscope_lookalike_urls_with_benchmark_dns(
    monkeypatch,
    url,
):
    """动态域名兼容不能放行仿冒主机、非官方 bucket 或不安全协议与端口。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda resolved_host: ["198.18.2.188"])

    with pytest.raises(ValueError, match="resolves to internal IP"):
        url_safety.validate_url(url)


def test_allows_configured_erp_oss_image_host_with_benchmark_dns(monkeypatch):
    """已审计的 ERP 产品图 OSS 域名在基准网段映射下仍应允许归档。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.1.119"])

    url_safety.validate_url(
        "https://xiumancloud.oss-cn-beijing.aliyuncs.com/products/cabinet.jpg"
    )


def test_rejects_untrusted_host_with_benchmark_dns(monkeypatch):
    """基准网段不能被普遍放行，未知域名仍必须被 SSRF 防护拒绝。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.1.118"])

    with pytest.raises(ValueError, match="resolves to internal IP"):
        url_safety.validate_url("https://untrusted.example.com/image.png")


def test_allows_wechat_article_host_with_benchmark_dns(monkeypatch):
    """公众号文章域名的 HTTPS 默认端口可兼容当前网络的基准网段映射。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.0.12"])

    url_safety.validate_url("https://mp.weixin.qq.com/s/example-article")


def test_rejects_wechat_article_host_without_https(monkeypatch):
    """微信域名的兼容例外不能降低协议要求。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.0.12"])

    with pytest.raises(ValueError, match="resolves to internal IP"):
        url_safety.validate_url("http://mp.weixin.qq.com/s/example-article")


def test_rejects_wechat_article_host_with_non_default_port(monkeypatch):
    """微信域名的兼容例外不能放开非默认服务端口。"""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_dns", lambda host: ["198.18.0.12"])

    with pytest.raises(ValueError, match="resolves to internal IP"):
        url_safety.validate_url("https://mp.weixin.qq.com:9443/s/example-article")
