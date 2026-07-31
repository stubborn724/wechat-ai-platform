"""ERP 产品素材服务测试。

这些测试固定外部 ERP 的 OAuth 与产品查询协议，确保后续调整页面或路由时，
不会意外暴露密钥、重复获取 Token，或丢失产品报价图地址。
"""

import asyncio
import base64
import json

import httpx
import pytest
from pydantic import ValidationError


def test_product_search_uses_oauth_token_and_normalizes_quote_image():
    """产品搜索应先获取 Token，再使用报价图作为可导入的素材地址。"""
    from app.services.erp_product_service import ErpProductClient, ErpProductSource

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/admin-api/system/oauth2/token":
            expected_basic = base64.b64encode(b"test-client:test-secret").decode("ascii")
            assert request.headers["Authorization"] == f"Basic {expected_basic}"
            assert request.headers["tenant-id"] == "1"
            return httpx.Response(200, json={
                "code": 0,
                "data": {"access_token": "erp-token", "expires_in": 3600},
            })

        assert request.url.path == "/open-api/erp/test/products/search"
        assert request.headers["Authorization"] == "Bearer erp-token"
        assert request.headers["tenant-id"] == "1"
        assert json.loads(request.content) == {"pageNo": 1, "pageSize": 10, "series": "写怀"}
        return httpx.Response(200, json={
            "success": True,
            "code": "0",
            "data": {
                "list": [{
                    "designName": "天鹅功能沙发",
                    "seriesNames": ["写怀"],
                    "productStyle": "功能性产品",
                    "commodityCategoryNames": ["沙发"],
                    "quoteImageUrl": "https://assets.example.com/swan.jpg",
                }],
                "total": 1,
                "pageNo": 1,
                "pageSize": 10,
            },
        })

    client = ErpProductClient(
        base_url="http://erp.example.com",
        tenant_id="1",
        sources=[ErpProductSource(
            key="xiehuai",
            name="写怀",
            client_id="test-client",
            client_secret="test-secret",
            product_api_path="/open-api/erp/test/products/search",
        )],
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(client.search_products("xiehuai", {"pageNo": 1, "pageSize": 10, "series": "写怀"}))

    assert result.total == 1
    assert result.products[0].name == "天鹅功能沙发"
    assert result.products[0].image_url == "https://assets.example.com/swan.jpg"
    assert result.products[0].tags == ["写怀", "沙发", "功能性产品"]
    assert len(requests) == 2


def test_product_search_reuses_valid_access_token():
    """同一来源连续查询时应复用有效 Token，避免无意义地消耗 ERP 授权接口额度。"""
    from app.services.erp_product_service import ErpProductClient, ErpProductSource

    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path == "/admin-api/system/oauth2/token":
            token_requests += 1
            return httpx.Response(200, json={
                "code": 0,
                "data": {"access_token": "cached-token", "expires_in": 3600},
            })
        return httpx.Response(200, json={
            "success": True,
            "code": "0",
            "data": {"list": [], "total": 0, "pageNo": 1, "pageSize": 10},
        })

    client = ErpProductClient(
        base_url="http://erp.example.com",
        tenant_id="1",
        sources=[ErpProductSource(
            key="jianzhi",
            name="剪纸系列",
            client_id="test-client",
            client_secret="test-secret",
            product_api_path="/open-api/erp/test/products/search",
        )],
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.search_products("jianzhi", {"pageNo": 1, "pageSize": 10}))
    asyncio.run(client.search_products("jianzhi", {"pageNo": 1, "pageSize": 10}))

    assert token_requests == 1


def test_product_search_uses_product_name_when_design_name_is_absent():
    """ERP 未返回 designName 时，产品名称仍应保留为可读的导入素材名称。"""
    from app.services.erp_product_service import ErpProductClient

    product = ErpProductClient._normalize_product({
        "productName": "云朵单椅",
        "productModel": "YD-001",
        "quoteImageUrl": "https://assets.example.com/cloud-chair.jpg",
    })

    assert product is not None
    assert product.name == "云朵单椅"


def test_erp_archive_filename_uses_product_name_for_library_display():
    """ERP 导入时应保留产品名，素材库不能展示自动归档的技术文件名。"""
    from app.services.asset_archive_service import build_archive_filename

    assert build_archive_filename("云朵单椅", ".jpg") == "云朵单椅.jpg"


def test_bulk_asset_delete_request_rejects_more_than_one_hundred_items():
    """批量删除必须有服务端上限，避免浏览器一次提交过多素材记录。"""
    from app.api.v1.assets import BulkDeleteAssetsRequest

    with pytest.raises(ValidationError):
        BulkDeleteAssetsRequest(asset_ids=list(range(1, 102)))


def test_product_search_rejects_unknown_source_without_requesting_erp():
    """来源键必须在服务端配置中存在，不能将任意路径或凭证交给客户端控制。"""
    from app.services.erp_product_service import ErpProductClient, ErpProductSource, ErpProductSourceNotFoundError

    client = ErpProductClient(
        base_url="http://erp.example.com",
        tenant_id="1",
        sources=[ErpProductSource(
            key="xiehuai",
            name="写怀",
            client_id="test-client",
            client_secret="test-secret",
            product_api_path="/open-api/erp/test/products/search",
        )],
    )

    with pytest.raises(ErpProductSourceNotFoundError, match="unknown"):
        asyncio.run(client.search_products("unknown", {"pageNo": 1, "pageSize": 10}))


def test_erp_network_disconnect_is_converted_to_business_error_without_response_attribute():
    """ERP 服务断连时异常可能没有 response，错误转换不能再次抛 AttributeError。"""
    from app.services.erp_product_service import ErpProductApiError, ErpProductClient, ErpProductSource

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("server disconnected")

    client = ErpProductClient(
        base_url="http://erp.example.com",
        tenant_id="1",
        sources=[ErpProductSource(
            key="zhongxiwujie",
            name="中西无界",
            client_id="test-client",
            client_secret="test-secret",
            product_api_path="/open-api/erp/test/products/search",
        )],
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ErpProductApiError, match="ERP 服务请求失败"):
        asyncio.run(client.search_products("zhongxiwujie", {"pageNo": 1, "pageSize": 10}))
