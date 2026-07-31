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
