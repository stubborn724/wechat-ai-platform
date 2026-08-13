"""TaGeAI 连接器账户服务。

本模块是微信平台接收 TaGeAI SaaS 管理端公众号凭据的唯一业务边界。它复用既有
``WeChatAccount`` 与 ``AccountCredential`` 存储，保证 AppSecret 只在此处加密落库，
不会返回 Gateway、桌面端或主 Agent。文章调用仍只使用不可猜测的 ``accountRef``。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from app.config import settings
from app.models.mysql_models import AccountCredential, WeChatAccount
from app.services.encryption_service import derive_key, encrypt_secret

_CONNECTOR_REFERENCE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{7,127}$")
_CONNECTOR_OWNER_USER_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")
_DELIVERY_MODES = {"DRAFT", "PUBLISH"}


class ConnectorAccountInputError(ValueError):
    """连接器账户输入、状态或租户边界不满足时抛出的稳定业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_connector_account_ref(value: str) -> str:
    """验证 SaaS 对外账号引用。

    引用是 Gateway 生成的稳定逻辑键，而不是微信平台内部自增 ID。禁止纯数字可以防止
    调用方把内部主键当作 API 参数猜测，也让后续账号迁移不影响 TaGeAI 任务历史。
    """

    normalized = str(value or "").strip()
    if not _CONNECTOR_REFERENCE_PATTERN.fullmatch(normalized) or normalized.isdigit():
        raise ConnectorAccountInputError(
            "INVALID_CONNECTOR_ACCOUNT_REF",
            "公众号连接标识格式无效",
        )
    return normalized


def validate_connector_owner_user_id(value: str | int) -> str:
    """验证由 Gateway 派生的个人连接所有者用户 ID。

    ``ownerUserId`` 是服务间已签名请求的一部分，而不是浏览器可指定的筛选条件。这里仍
    拒绝空值、负数和非数字，避免旧的企业级连接在个人模式下被任何用户命中。
    """

    normalized = str(value or "").strip()
    if not _CONNECTOR_OWNER_USER_ID_PATTERN.fullmatch(normalized):
        raise ConnectorAccountInputError("INVALID_CONNECTOR_OWNER", "公众号连接所有者无效")
    return normalized


def build_connector_account_summary(account: Any) -> dict[str, Any]:
    """将内部公众号账户投影为可安全返回 TaGeAI 的摘要。

    ``capabilities`` 是既有账户表中可扩展的非敏感元数据。只有带有连接器引用的账户
    才能被 SaaS 调度，避免历史后台账户在未授予 TaGeAI 管理权的情况下被误发文章。
    """

    capabilities = account.capabilities if isinstance(account.capabilities, dict) else {}
    account_ref = capabilities.get("tageai_connector_ref")
    if not isinstance(account_ref, str):
        raise ConnectorAccountInputError("CONNECTOR_REFERENCE_MISSING", "公众号尚未注册为 TaGeAI 连接器")
    normalized_ref = validate_connector_account_ref(account_ref)
    delivery_modes = _normalize_delivery_modes(capabilities.get("delivery_modes"))
    return {
        "accountRef": normalized_ref,
        "displayName": str(account.name or "").strip(),
        "status": "ACTIVE" if str(account.status or "").lower() == "active" else "INACTIVE",
        "capabilities": delivery_modes,
        "lastVerifiedAt": _format_timestamp(getattr(account, "last_health_at", None)),
        "errorCode": "WECHAT_CREDENTIAL_INVALID" if getattr(account, "last_health_error", None) else None,
        "errorMessage": _safe_error_message(getattr(account, "last_health_error", None)),
    }


def upsert_connector_account(
    db: Any,
    *,
    tenant_id: int,
    owner_user_id: str | int,
    account_ref: str,
    display_name: str,
    app_id: str,
    app_secret: str,
    delivery_modes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """创建或更新由 TaGeAI 管理的公众号连接。

    调用方在进入本服务前已完成 HMAC 身份校验。这里仍显式限制租户和引用，并用账户
    ``capabilities`` 保存逻辑映射，避免增加一张明文或重复凭据表。凭据写入后立即更新
    健康状态；微信开放平台连通性验证由路由注入的验证器在事务提交前执行。
    """

    normalized_ref = validate_connector_account_ref(account_ref)
    normalized_owner_user_id = validate_connector_owner_user_id(owner_user_id)
    normalized_name = str(display_name or "").strip()
    normalized_app_id = str(app_id or "").strip()
    normalized_secret = str(app_secret or "").strip()
    if not normalized_name or not normalized_app_id or not normalized_secret:
        raise ConnectorAccountInputError("CONNECTOR_CREDENTIAL_REQUIRED", "公众号名称、AppID 和 AppSecret 均不能为空")

    account = _find_account_by_reference(db, tenant_id, normalized_owner_user_id, normalized_ref)
    if account is None:
        account = WeChatAccount(
            tenant_id=tenant_id,
            name=normalized_name,
            app_id=normalized_app_id,
            auth_mode="credential",
            status="active",
            capabilities={},
        )
        db.add(account)
        db.flush()
    else:
        account.name = normalized_name
        account.app_id = normalized_app_id
        account.auth_mode = "credential"
        account.status = "active"
        account.deleted_at = None

    account.capabilities = {
        **(account.capabilities if isinstance(account.capabilities, dict) else {}),
        "tageai_connector_ref": normalized_ref,
        "tageai_connector_owner_user_id": normalized_owner_user_id,
        "delivery_modes": _normalize_delivery_modes(delivery_modes),
    }
    account.last_health_at = datetime.now(timezone.utc)
    account.last_health_error = None
    _replace_account_secret(db, tenant_id, account.id, normalized_secret)
    db.flush()
    return build_connector_account_summary(account)


def list_connector_accounts(db: Any, tenant_id: int, owner_user_id: str | int) -> list[dict[str, Any]]:
    """返回当前用户在当前租户可被 TaGeAI 调度的非敏感公众号账号列表。"""

    normalized_owner_user_id = validate_connector_owner_user_id(owner_user_id)

    accounts = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == tenant_id,
        WeChatAccount.deleted_at.is_(None),
    ).order_by(WeChatAccount.id.asc()).all()
    summaries: list[dict[str, Any]] = []
    for account in accounts:
        try:
            summary = build_connector_account_summary(account)
        except ConnectorAccountInputError:
            continue
        capabilities = account.capabilities if isinstance(account.capabilities, dict) else {}
        if capabilities.get("tageai_connector_owner_user_id") != normalized_owner_user_id:
            continue
        if summary["status"] == "ACTIVE":
            summaries.append(summary)
    return summaries


def disable_connector_account(db: Any, *, tenant_id: int, owner_user_id: str | int, account_ref: str) -> dict[str, Any]:
    """停用连接器账号而不删除凭据或历史任务，供审计和故障恢复使用。"""

    account = _find_account_by_reference(
        db, tenant_id, validate_connector_owner_user_id(owner_user_id), validate_connector_account_ref(account_ref),
    )
    if account is None:
        raise ConnectorAccountInputError("CONNECTOR_ACCOUNT_NOT_FOUND", "公众号连接不存在")
    account.status = "inactive"
    account.last_health_at = datetime.now(timezone.utc)
    db.flush()
    return build_connector_account_summary(account)


def resolve_connector_account_id(
    accounts: Iterable[Any], *, tenant_id: int, owner_user_id: str | int, account_ref: str,
) -> int | None:
    """从当前租户的公众号集合解析连接器引用对应的内部账号 ID。

    该函数刻意要求调用方已经按租户查询账户，仍再次校验 ``tenant_id``，形成执行前的
    双重租户防线。返回 ``None`` 而不是选择第一个账号，使 Gateway 可以稳定映射为
    ``ACCOUNT_NOT_BOUND``，绝不让发布任务落入不确定目标。
    """

    normalized_ref = validate_connector_account_ref(account_ref)
    normalized_owner_user_id = validate_connector_owner_user_id(owner_user_id)
    for account in accounts:
        if getattr(account, "tenant_id", None) != tenant_id or getattr(account, "deleted_at", None) is not None:
            continue
        capabilities = account.capabilities if isinstance(getattr(account, "capabilities", None), dict) else {}
        if capabilities.get("tageai_connector_ref") == normalized_ref \
                and capabilities.get("tageai_connector_owner_user_id") == normalized_owner_user_id:
            return int(account.id)
    return None


def _find_account_by_reference(
    db: Any, tenant_id: int, owner_user_id: str, account_ref: str,
) -> WeChatAccount | None:
    """按租户、个人所有者和 JSON 逻辑键定位账号，永不跨用户或模糊匹配。"""

    # MySQL JSON 查询在不同部署版本的方言兼容性不稳定，因此先受租户边界约束读取再
    # 精确比较内存 JSON。企业每租户公众号数量很小，连接器管理路径不在高频执行热路径。
    accounts = db.query(WeChatAccount).filter(
        WeChatAccount.tenant_id == tenant_id,
        WeChatAccount.deleted_at.is_(None),
    ).all()
    account_id = resolve_connector_account_id(
        accounts, tenant_id=tenant_id, owner_user_id=owner_user_id, account_ref=account_ref,
    )
    if account_id is not None:
        for account in accounts:
            if account.id == account_id:
                return account
    return None


def _replace_account_secret(db: Any, tenant_id: int, account_id: int, app_secret: str) -> None:
    """更新既有加密凭据记录，确保一条公众号在当前租户只保留当前密钥版本。"""

    credential = db.query(AccountCredential).filter(
        AccountCredential.tenant_id == tenant_id,
        AccountCredential.account_id == account_id,
    ).first()
    encrypted_secret = encrypt_secret(app_secret, derive_key(settings.credential_key))
    if credential is None:
        db.add(AccountCredential(
            tenant_id=tenant_id,
            account_id=account_id,
            encrypted_secret=encrypted_secret,
            key_version="v1",
        ))
        return
    credential.encrypted_secret = encrypted_secret
    credential.key_version = "v1"


def _normalize_delivery_modes(value: Iterable[str] | None) -> list[str]:
    """标准化投递能力，缺省开放草稿和发布两项由 Gateway 再做角色确认。"""

    modes = {str(item).upper() for item in (value or _DELIVERY_MODES) if str(item).upper() in _DELIVERY_MODES}
    return sorted(modes or {"DRAFT"})


def _format_timestamp(value: Any) -> str | None:
    """将数据库时间投影为 ISO-8601 字符串，空值保持为空。"""

    return value.isoformat() if isinstance(value, datetime) else None


def _safe_error_message(value: Any) -> str | None:
    """限制错误摘要长度，防止平台异常文本携带凭据后进入 TaGeAI 页面。"""

    message = str(value or "").strip()
    return message[:500] if message else None
