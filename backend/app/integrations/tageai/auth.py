"""TaGeAI Integration API HMAC 签名认证。

认证流程：
1. 从请求头提取 X-TageAI-Client-Id、X-TageAI-Timestamp、X-TageAI-Nonce、X-TageAI-Signature
2. 根据 clientId 查找对应的租户绑定和签名密钥
3. 校验时间戳（与服务器时间偏差不超过 5 分钟）
4. 校验 Nonce 不重复（防重放）
5. 重新计算签名并与请求头中的签名比对
6. 签名一致且时间窗口内 → 认证通过，返回 tenant_id 和 binding 上下文

边界：
- 签名失败返回 401，不泄露具体密钥信息
- 时间戳过期返回 401，附带服务器当前时间供客户端校准
- Nonce 重复返回 401，可能为重放攻击
- 此认证只作用于 /integrations/tageai 路由，不影响普通 JWT 路由
"""

import hashlib
import hmac
import logging
import time
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from fastapi import Header, HTTPException, Request, status
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)

# 时间窗口：5 分钟（300 秒）
TIMESTAMP_WINDOW_SECONDS = 300

async def verify_tageai_signature(
    request: Request,
    x_tageai_client_id: str = Header(..., alias="X-TageAI-Client-Id"),
    x_tageai_timestamp: str = Header(..., alias="X-TageAI-Timestamp"),
    x_tageai_nonce: str = Header(..., alias="X-TageAI-Nonce"),
    x_tageai_signature: str = Header(..., alias="X-TageAI-Signature"),
) -> dict:
    """验证 TaGeAI Integration API 请求签名。

    Returns:
        dict: 包含客户端、租户绑定、租户 ID 与服务端账号绑定映射的可信上下文。
    Raises:
        HTTPException: 签名无效、时间戳过期或 Nonce 重复
    """
    # 1. 查找 client 配置
    client_config = _find_client_config(x_tageai_client_id)
    if client_config is None:
        logger.warning("TageAI auth: unknown client_id=%s", x_tageai_client_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown client credentials",
        )

    signing_secret = client_config["signing_secret"]
    tenant_binding_id = client_config["tenant_binding_id"]
    tenant_id = client_config["tenant_id"]

    # 2. 校验时间戳
    try:
        timestamp = int(x_tageai_timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid timestamp format",
        )

    now = int(time.time())
    if abs(now - timestamp) > TIMESTAMP_WINDOW_SECONDS:
        logger.warning(
            "TageAI auth: timestamp expired client_id=%s ts=%d now=%d diff=%d",
            x_tageai_client_id, timestamp, now, abs(now - timestamp),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Timestamp expired. Server time: {now}",
        )

    # 3. 重新计算签名并比对。await request.body() 会使用 Starlette 的缓存，
    # 不会消耗后续 Pydantic 请求体解析所需的内容；不能读取私有 _body 属性，
    # 否则依赖执行顺序变化时会错误地把非空请求签成空请求。
    body_bytes = await request.body()

    canonical_path, canonical_query = _canonicalize_request_target(request)
    expected_signature = _build_signature(
        signing_secret=signing_secret,
        client_id=x_tageai_client_id,
        method=request.method,
        canonical_path=canonical_path,
        canonical_query=canonical_query,
        timestamp=x_tageai_timestamp,
        nonce=x_tageai_nonce,
        body_bytes=body_bytes,
    )

    # 签名格式: sha256=<hex>
    received_signature = x_tageai_signature
    if received_signature.startswith("sha256="):
        received_signature = received_signature[7:]

    if not hmac.compare_digest(expected_signature, received_signature):
        logger.warning("TageAI auth: signature mismatch client_id=%s", x_tageai_client_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # 4. 只在签名成功后登记 Nonce。Redis SET NX EX 是单条原子命令，多个 API
    # Worker、重启后的新进程都共享去重事实；Redis 不可用时 fail-closed，不能退化
    # 为仅当前进程可见的内存字典，否则跨 Worker 重放仍能成功。
    try:
        nonce_claimed = claim_tageai_nonce(
            _get_nonce_store(), x_tageai_client_id, x_tageai_nonce,
        )
    except RedisError as exc:
        logger.error("TageAI auth: replay protection unavailable client_id=%s", x_tageai_client_id, exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Replay protection unavailable",
        ) from exc
    if not nonce_claimed:
        logger.warning("TageAI auth: duplicate nonce client_id=%s", x_tageai_client_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Duplicate nonce",
        )

    logger.info("TageAI auth: verified client_id=%s tenant_id=%d", x_tageai_client_id, tenant_id)
    return {
        "client_id": x_tageai_client_id,
        "tenant_binding_id": tenant_binding_id,
        "tenant_id": tenant_id,
        # 账号映射只来自服务端连接配置，绝不接受调用请求携带本地账号 ID。即使某个
        # 租户只有一个公众号，也不能回退选择“第一个账号”，否则未来扩容后可能误发。
        "target_account_bindings": client_config.get("target_account_bindings") or {},
    }


def _build_signature(
    signing_secret: str,
    client_id: str,
    method: str,
    canonical_path: str,
    canonical_query: str,
    timestamp: str,
    nonce: str,
    body_bytes: bytes,
) -> str:
    """计算 HMAC-SHA256 签名。

    签名字符串 = clientId + method + path + canonicalQuery + timestamp + nonce + bodyHash。
    把 HTTP 方法和规范化请求目标纳入负载后，攻击者即使获取空 GET 的签名，也无法把
    它重放到 POST 取消接口或同一路径的不同查询参数。该格式须与 TaGeAI Gateway
    ``WechatPlatformExecutionAdapter`` 同步升级。
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    payload = ":".join((
        client_id,
        str(method or "").upper(),
        canonical_path,
        canonical_query,
        timestamp,
        nonce,
        body_hash,
    ))
    signature = hmac.new(
        signing_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


def _canonicalize_request_target(request: Request) -> tuple[str, str]:
    """把 ASGI 请求转换成跨语言稳定的路径和查询串签名分量。

    ``parse_qsl`` 后按键和值排序，可以避免等价参数仅因顺序不同得到不同签名；保持空
    值则防止 ``?flag=`` 与缺少 ``flag`` 被混为一谈。路径始终使用 ASGI 已解码的
    ``scope.path``，不接受 Host、协议或片段，避免反向代理重写导致签名语义漂移。
    """

    canonical_path = str(request.scope.get("path") or request.url.path or "")
    raw_query = request.scope.get("query_string") or b""
    if isinstance(raw_query, bytes):
        raw_query_text = raw_query.decode("utf-8", errors="strict")
    else:
        raw_query_text = str(raw_query)
    query_pairs = parse_qsl(raw_query_text, keep_blank_values=True)
    canonical_query = urlencode(sorted(query_pairs), doseq=True, safe="~")
    return canonical_path, canonical_query


def _get_nonce_store() -> Redis:
    """创建复用连接池的 Redis 客户端，用于跨进程 HMAC nonce 去重。"""

    return Redis.from_url(settings.redis_url, decode_responses=True)


def claim_tageai_nonce(store: Redis, client_id: str, nonce: str) -> bool:
    """原子声明一个 nonce，返回 ``False`` 表示当前时间窗口内已经被使用过。"""

    nonce_key = f"tageai:integration:nonce:{client_id}:{nonce}"
    return bool(store.set(nonce_key, "1", nx=True, ex=TIMESTAMP_WINDOW_SECONDS))


def _find_client_config(client_id: str) -> Optional[dict]:
    """根据 clientId 查找租户绑定配置。

    第一期从 settings 中读取单一配置；
    后续可扩展为数据库查询支持多租户。
    """
    # 第一期：单一绑定
    cfg = settings.tageai_integration_clients
    if not cfg:
        return None

    # cfg 格式: {"client_id": "xxx", "signing_secret": "yyy",
    #             "tenant_binding_id": "tenant-binding-1", "tenant_id": 1,
    #             "target_account_bindings": {"tenant-wechat-account-1": 101}}
    #
    # target_account_bindings 的键是 TaGeAI 对用户可见的稳定账号引用，值是本平台
    # 的内部 WeChatAccount ID。该映射属于部署级密钥/连接配置的一部分，不能透传给
    # 浏览器或写进调用请求体。
    if isinstance(cfg, dict) and cfg.get("client_id") == client_id:
        return cfg

    # 也支持列表格式
    if isinstance(cfg, list):
        for c in cfg:
            if c.get("client_id") == client_id:
                return c

    return None


def find_tageai_client_config_by_binding(tenant_binding_id: str) -> Optional[dict]:
    """按已验签的租户绑定查找出站回调配置。

    回调 URL 和签名密钥属于部署连接配置，不能由 Invocation 请求体指定。通过同一个绑定
    查找使入站创建和出站状态回传使用同一安全边界，避免把某租户的完成事件投递到其他 Gateway。
    """

    normalized_binding_id = str(tenant_binding_id or "").strip()
    if not normalized_binding_id:
        return None
    cfg = settings.tageai_integration_clients
    if isinstance(cfg, dict):
        return cfg if cfg.get("tenant_binding_id") == normalized_binding_id else None
    if isinstance(cfg, list):
        for item in cfg:
            if isinstance(item, dict) and item.get("tenant_binding_id") == normalized_binding_id:
                return item
    return None
