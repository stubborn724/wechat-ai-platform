"""ERP 产品查询服务。

本模块只负责 ERP OAuth 授权、产品分页查询和响应规范化。路由层不接触
ERP 凭证，前端也只能传来源键和筛选条件，从而避免客户端篡改查询路径或泄露密钥。
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

# 路由请求之间复用客户端，才能让 OAuth Token 缓存真正生效。配置变更时通过
# 配置签名重新创建，开发环境修改 .env 后重启服务同样会得到新实例。
_configured_client: Optional["ErpProductClient"] = None
_configured_client_signature: Optional[tuple[str, str, str]] = None


class ErpProductError(RuntimeError):
    """ERP 产品接口的可预期业务错误基类。"""


class ErpProductSourceNotFoundError(ErpProductError):
    """请求的品牌来源不存在，防止客户端指定任意 ERP 路径。"""


class ErpProductConfigurationError(ErpProductError):
    """ERP 来源配置不完整或 JSON 结构不合法。"""


class ErpProductApiError(ErpProductError):
    """ERP 授权或产品查询接口返回失败。"""


@dataclass(frozen=True)
class ErpProductSource:
    """一个品牌对应的一组 ERP 应用凭证与产品查询路径。"""

    key: str
    name: str
    client_id: str
    client_secret: str
    product_api_path: str


@dataclass(frozen=True)
class ErpProduct:
    """归一化后的产品记录，仅暴露文章选图真正需要的字段。"""

    name: str
    image_url: str
    series: List[str]
    style: str
    categories: List[str]
    tags: List[str]


@dataclass(frozen=True)
class ErpProductPage:
    """ERP 分页查询结果，保留分页信息供页面继续加载。"""

    products: List[ErpProduct]
    total: int
    page_no: int
    page_size: int


@dataclass(frozen=True)
class _CachedToken:
    """缓存的访问令牌及其单调时钟过期时间。"""

    value: str
    expires_at: float


def parse_erp_product_sources(raw_sources: str) -> List[ErpProductSource]:
    """解析环境变量中的品牌配置，并在启动使用前明确校验必要字段。

    使用 JSON 配置而非硬编码品牌的原因是品牌、ERP 应用和路径可独立增加，
    无须为新增公众号改动发布业务代码。
    """
    try:
        records = json.loads(raw_sources or "[]")
    except json.JSONDecodeError as exc:
        raise ErpProductConfigurationError("ERP_PRODUCT_SOURCES_JSON 不是合法 JSON") from exc

    if not isinstance(records, list):
        raise ErpProductConfigurationError("ERP_PRODUCT_SOURCES_JSON 必须是 JSON 数组")

    sources: List[ErpProductSource] = []
    known_keys = set()
    required_fields = ("key", "name", "client_id", "client_secret", "product_api_path")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ErpProductConfigurationError(f"第 {index + 1} 个 ERP 来源必须是对象")
        missing = [field for field in required_fields if not str(record.get(field, "")).strip()]
        if missing:
            raise ErpProductConfigurationError(
                f"第 {index + 1} 个 ERP 来源缺少配置：{', '.join(missing)}"
            )
        key = str(record["key"]).strip()
        if key in known_keys:
            raise ErpProductConfigurationError(f"ERP 来源键重复：{key}")
        known_keys.add(key)
        sources.append(ErpProductSource(
            key=key,
            name=str(record["name"]).strip(),
            client_id=str(record["client_id"]).strip(),
            client_secret=str(record["client_secret"]).strip(),
            product_api_path=str(record["product_api_path"]).strip(),
        ))
    return sources


class ErpProductClient:
    """面向多个品牌 ERP 应用的异步客户端。

    Token 缓存按来源键隔离，避免某个品牌的 Token 错用于另一个品牌；测试可注入
    ``httpx.MockTransport``，因此不会把网络协议测试绑定到真实 ERP 环境。
    """

    _TOKEN_SAFETY_WINDOW_SECONDS = 60

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        sources: Iterable[ErpProductSource],
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if not base_url.strip():
            raise ErpProductConfigurationError("未配置 ERP_PRODUCT_API_BASE_URL")
        self._base_url = base_url.rstrip("/")
        self._tenant_id = str(tenant_id).strip()
        self._sources = {source.key: source for source in sources}
        self._transport = transport
        self._tokens: Dict[str, _CachedToken] = {}

    def list_sources(self) -> List[ErpProductSource]:
        """返回无密钥的来源配置，供路由层构造品牌下拉列表。"""
        return list(self._sources.values())

    def get_source(self, source_key: str) -> ErpProductSource:
        """按服务端配置取得来源，拒绝未知来源键。"""
        source = self._sources.get(source_key)
        if not source:
            raise ErpProductSourceNotFoundError(f"ERP 产品来源不存在：{source_key}")
        return source

    async def search_products(self, source_key: str, filters: Mapping[str, Any]) -> ErpProductPage:
        """获取 Token 后查询来源对应的产品分页数据。"""
        source = self.get_source(source_key)
        token = await self._get_access_token(source)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "tenant-id": self._tenant_id,
        }
        response = await self._post(source.product_api_path, headers=headers, json_body=dict(filters))
        payload = self._read_json(response, "产品查询")
        if not payload.get("success") or str(payload.get("code")) != "0":
            raise ErpProductApiError(f"ERP 产品查询失败：{payload.get('message') or payload.get('msg') or '未知错误'}")

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ErpProductApiError("ERP 产品查询返回 data 格式错误")
        records = data.get("list") or []
        if not isinstance(records, list):
            raise ErpProductApiError("ERP 产品查询返回 list 格式错误")

        products = [product for product in (self._normalize_product(record) for record in records) if product]
        return ErpProductPage(
            products=products,
            total=self._as_int(data.get("total"), len(products)),
            page_no=self._as_int(data.get("pageNo"), self._as_int(filters.get("pageNo"), 1)),
            page_size=self._as_int(data.get("pageSize"), self._as_int(filters.get("pageSize"), 10)),
        )

    async def _get_access_token(self, source: ErpProductSource) -> str:
        """获得来源专属 Token，提前一分钟刷新以避免请求中途过期。"""
        cached = self._tokens.get(source.key)
        if cached and cached.expires_at > time.monotonic() + self._TOKEN_SAFETY_WINDOW_SECONDS:
            return cached.value

        encoded_credentials = base64.b64encode(
            f"{source.client_id}:{source.client_secret}".encode("utf-8")
        ).decode("ascii")
        response = await self._post(
            "/admin-api/system/oauth2/token?grant_type=client_credentials&scope=erp.product.read",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "tenant-id": self._tenant_id,
            },
        )
        payload = self._read_json(response, "Token 获取")
        data = payload.get("data") or {}
        access_token = str(data.get("access_token") or "").strip()
        if payload.get("code") not in (0, "0") or not access_token:
            raise ErpProductApiError(f"ERP Token 获取失败：{payload.get('msg') or '未返回 access_token'}")

        expires_in = max(self._as_int(data.get("expires_in"), 3600), self._TOKEN_SAFETY_WINDOW_SECONDS + 1)
        self._tokens[source.key] = _CachedToken(
            value=access_token,
            expires_at=time.monotonic() + expires_in,
        )
        return access_token

    async def _post(
        self,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> httpx.Response:
        """统一执行 ERP POST 请求，集中控制超时和错误转换。"""
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), transport=self._transport) as client:
                response = await client.post(url, headers=dict(headers), json=json_body)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            # 连接中断、DNS 失败等传输异常没有 ``response`` 属性；使用两层
            # getattr 才能把所有 httpx 异常稳定转换为可供定时任务重试的业务错误。
            response = getattr(exc, "response", None)
            logger.warning(
                "ERP 请求失败：path=%s status=%s error=%s",
                path,
                getattr(response, "status_code", None),
                type(exc).__name__,
            )
            raise ErpProductApiError(f"ERP 服务请求失败：{exc}") from exc

    @staticmethod
    def _read_json(response: httpx.Response, operation: str) -> Dict[str, Any]:
        """检查 ERP JSON 响应，避免把非 JSON 错页传给业务层。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise ErpProductApiError(f"ERP {operation}返回了非 JSON 内容") from exc
        if not isinstance(payload, dict):
            raise ErpProductApiError(f"ERP {operation}响应格式错误")
        return payload

    @staticmethod
    def _normalize_product(record: Any) -> Optional[ErpProduct]:
        """将 ERP 产品主记录转换为前端选图所需的稳定小模型。

        当前公开接口只保证 ``quoteImageUrl``，没有报价图的记录无法导入，
        因此主动过滤，避免页面出现不可选的空卡片。
        """
        if not isinstance(record, dict):
            return None
        image_url = str(record.get("quoteImageUrl") or "").strip()
        if not image_url:
            return None
        series = ErpProductClient._string_list(record.get("seriesNames"))
        categories = ErpProductClient._string_list(record.get("commodityCategoryNames"))
        style = str(record.get("productStyle") or "").strip()
        # 不同 ERP 版本对产品名称字段的命名并不一致。按稳定的优先级取值，
        # 既优先使用设计名称，也兼容产品名、型号等字段，避免可用产品被标成未命名。
        name = ErpProductClient._first_text(
            record,
            "designName",
            "productName",
            "name",
            "productModel",
            "modelName",
            "skuName",
            "itemName",
        ) or "未命名产品"
        tags = list(dict.fromkeys([*series, *categories, *([style] if style else [])]))
        return ErpProduct(
            name=name,
            image_url=image_url,
            series=series,
            style=style,
            categories=categories,
            tags=tags,
        )

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        """兼容 ERP 的数组字段，并过滤空字符串。"""
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _first_text(record: Mapping[str, Any], *field_names: str) -> str:
        """按字段优先级返回第一个非空文本，集中兼容 ERP 的同义字段。"""
        for field_name in field_names:
            value = str(record.get(field_name) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        """将 ERP 可能返回的字符串数字安全转为整数。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def build_erp_product_client_from_settings() -> ErpProductClient:
    """从全局配置构建客户端。

    该工厂保持路由层无配置解析职责，并确保敏感字段永远不会进入 API 响应。
    """
    from app.config import settings

    global _configured_client, _configured_client_signature

    signature = (
        settings.erp_product_api_base_url,
        settings.erp_product_tenant_id,
        settings.erp_product_sources_json,
    )
    if _configured_client is None or _configured_client_signature != signature:
        _configured_client = ErpProductClient(
            base_url=settings.erp_product_api_base_url,
            tenant_id=settings.erp_product_tenant_id,
            sources=parse_erp_product_sources(settings.erp_product_sources_json),
        )
        _configured_client_signature = signature
    return _configured_client
