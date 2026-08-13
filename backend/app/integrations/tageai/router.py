"""TaGeAI Integration API 路由。

提供外部调用生命周期管理：
- POST   /invocations              创建调用（202 Accepted）
- GET    /invocations/{id}          查询状态
- POST   /invocations/{id}/cancel   取消任务
- POST   /callbacks                 接收回调（由微信平台内部任务完成后触发）

认证：所有端点的认证依赖为 verify_tageai_signature，
不经过普通 JWT 用户认证中间件。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.integrations.tageai.auth import verify_tageai_signature
from app.database import get_mysql_db
from app.integrations.tageai.service import (
    IntegrationInputError,
    IntegrationIdempotencyConflictError,
    cancel_invocation,
    create_invocation,
    get_invocation,
    list_pending_callbacks,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ============================================================================
# Schemas
# ============================================================================


class ReferenceInput(BaseModel):
    type: str = Field(..., description="url（对外协议仅支持平台安全抓取的 URL；text 与 asset_ref 不支持）")
    value: str = Field(..., description="参考内容")


class InvocationInput(BaseModel):
    reference: Optional[ReferenceInput] = None
    topic: Optional[str] = Field(None, max_length=500)
    style_notes: str = Field("", alias="styleNotes", max_length=2000)
    title_override: Optional[str] = Field(None, alias="titleOverride", max_length=200)
    content_constraints: list[str] = Field(default_factory=list, alias="contentConstraints")
    # 这些字段来自微信公众号工作台的真实配置，只有用户/Agent 明确选择时才会进入请求。
    watermark_enabled: Optional[bool] = None
    image_source: Optional[str] = None
    enabled_image_methods: Optional[List[str]] = None
    article_count: Optional[int] = None
    knowledge_base_ids: Optional[List[int]] = None
    source_feed_id: Optional[int] = None
    feed_article_ids: Optional[List[int]] = None
    selected_image_urls: Optional[List[str]] = None
    selected_cover_image_url: Optional[str] = None
    footer_template: Optional[str] = None
    duration_sec: Optional[int] = None
    aspect_ratio: Optional[str] = None
    # 生成规模由服务端统一预算服务校验。默认值不要求交互，超过默认值必须携带本轮
    # Agent 交互得到的一次性批准；绝对上限仍由服务端拒绝，不能信任前端的确认结果。
    article_character_limit: Optional[int] = Field(None, alias="articleCharacterLimit")
    image_count: Optional[int] = Field(None, alias="imageCount")
    video_count: Optional[int] = Field(None, alias="videoCount")
    video_duration_seconds: Optional[int] = Field(None, alias="videoDurationSeconds")
    budget_approval: Optional[Dict[str, int]] = Field(None, alias="budgetApproval")
    # 该字段只在正式发布工具中出现，值必须来自当前租户先前成功生成的只读预览。
    # 平台服务会再次按租户、公众号、版本和有效期校验，不能把它当作直接发布凭据。
    publish_candidate_id: Optional[str] = Field(None, alias="publishCandidateId", min_length=8, max_length=96)


class CreateInvocationRequest(BaseModel):
    invocation_id: str = Field(..., alias="invocationId")
    tenant_binding_id: str = Field(..., alias="tenantBindingId")
    operation: str = Field(..., description="generate | imitate")
    delivery_mode: str = Field("DRAFT", alias="deliveryMode")
    target_account_ref: str = Field(..., alias="targetAccountRef", min_length=1, max_length=255)
    owner_user_id: str = Field(..., alias="ownerUserId", min_length=1, max_length=20)
    input: InvocationInput
    execution_id: str = Field(..., alias="executionId")
    callback_profile_id: Optional[str] = Field(None, alias="callbackProfileId")


class ArticlePreviewResult(BaseModel):
    """平台向桌面端投影的只读文章工件。

    预览不是文章编辑 API，也不是内容下载接口；字段上限与 Electron 主进程保持一致，
    让不可信或异常大的下游数据在进入聊天工作区前被明确拒绝。
    """

    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=50_000)
    cover_image_url: Optional[str] = Field(None, alias="coverImageUrl", max_length=2_048)


class InvocationResult(BaseModel):
    title: Optional[str] = None
    content_ref: Optional[str] = Field(None, alias="contentRef")
    cover_image_ref: Optional[str] = Field(None, alias="coverImageRef")
    draft_id: Optional[str] = Field(None, alias="draftId")
    publish_id: Optional[str] = Field(None, alias="publishId")
    article_url: Optional[str] = Field(None, alias="articleUrl")
    article_preview: Optional[ArticlePreviewResult] = Field(None, alias="articlePreview")
    publish_candidate_id: Optional[str] = Field(None, alias="publishCandidateId")
    publish_candidate_expires_at: Optional[str] = Field(None, alias="publishCandidateExpiresAt")


class InvocationError(BaseModel):
    error_code: str = Field(..., alias="errorCode")
    message: str
    retryable: bool = False
    details: Optional[dict] = None


class InvocationResponse(BaseModel):
    invocation_id: str = Field(..., alias="invocationId")
    external_job_id: Optional[str] = Field(None, alias="externalJobId")
    status: str
    phase: str
    progress: int = 0
    platform: Optional[str] = None
    platform_label: Optional[str] = Field(None, alias="platformLabel")
    media_summary: Optional[dict] = Field(None, alias="mediaSummary")
    estimated_remaining_seconds: Optional[dict] = Field(None, alias="estimatedRemainingSeconds")
    heartbeat_at: Optional[str] = Field(None, alias="heartbeatAt")
    result: Optional[InvocationResult] = None
    error: Optional[InvocationError] = None
    created_at: Optional[str] = Field(None, alias="createdAt")
    started_at: Optional[str] = Field(None, alias="startedAt")
    finished_at: Optional[str] = Field(None, alias="finishedAt")


class CallbackRequest(BaseModel):
    invocation_id: str = Field(..., alias="invocationId")
    external_job_id: str = Field(..., alias="externalJobId")
    status: str
    phase: str
    progress: int = 0
    result: Optional[InvocationResult] = None
    error: Optional[InvocationError] = None
    event_id: str = Field(..., alias="eventId")
    event_time: str = Field(..., alias="eventTime")
    tenant_binding_id: Optional[str] = Field(None, alias="tenantBindingId")


class CreateConnectorAccountRequest(BaseModel):
    """TaGeAI 管理端提交的公众号连接信息。

    AppSecret 只在本次受 HMAC 保护的服务间请求内出现。模型不会复用于列表、调用或回调
    响应，从类型边界防止凭据被错误回显到浏览器或主 Agent 上下文。
    """

    connector_account_ref: str = Field(..., alias="connectorAccountRef", min_length=8, max_length=128)
    owner_user_id: str = Field(..., alias="ownerUserId", min_length=1, max_length=20)
    display_name: str = Field(..., alias="displayName", min_length=1, max_length=128)
    app_id: str = Field(..., alias="appId", min_length=1, max_length=128)
    app_secret: str = Field(..., alias="appSecret", min_length=1, max_length=512)
    delivery_modes: list[str] = Field(default_factory=lambda: ["DRAFT", "PUBLISH"], alias="deliveryModes")


# ============================================================================
# Routes
# ============================================================================


@router.post("/invocations", status_code=status.HTTP_202_ACCEPTED)
async def create(
    request: CreateInvocationRequest,
    auth_ctx: dict = Depends(verify_tageai_signature),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    """创建外部调用。

    接收 TaGeAI Gateway 的请求，创建微信平台内部任务（ContentJob 或 ImitationTask），
    返回 202 Accepted 表示已接受，不表示任务已完成。

    幂等：同一 invocationId 重复请求返回已有记录。
    """
    tenant_id = auth_ctx["tenant_id"]
    _require_tenant_binding(request.tenant_binding_id, auth_ctx)

    logger.info(
        "Integration create: invocationId=%s operation=%s deliveryMode=%s tenantId=%d",
        request.invocation_id, request.operation, request.delivery_mode, tenant_id,
    )

    try:
        invocation = create_invocation(
            invocation_id=request.invocation_id,
            tenant_id=tenant_id,
            operation=request.operation,
            delivery_mode=request.delivery_mode,
            target_account_ref=request.target_account_ref,
            owner_user_id=request.owner_user_id,
            input_data=request.input.model_dump(by_alias=False),
            execution_id=request.execution_id,
            # 租户与账号映射由已经验签的服务端连接派生。请求体的账号引用仅是一个
            # 非敏感逻辑键，绝不能自行指定本地公众号账号 ID。
            tenant_binding_id=auth_ctx["tenant_binding_id"],
            target_account_bindings=auth_ctx.get("target_account_bindings"),
            idempotency_key=idempotency_key,
        )
    except IntegrationInputError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "errorCode": exc.code, "message": str(exc), "retryable": False,
            **({"details": exc.details} if exc.details else {}),
        }) from exc
    except IntegrationIdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "errorCode": "IDEMPOTENCY_PAYLOAD_CONFLICT", "message": str(exc), "retryable": False,
        }) from exc

    return _to_response(invocation)


@router.get("/invocations/{invocation_id}")
async def query(invocation_id: str, auth_ctx: dict = Depends(verify_tageai_signature)):
    """查询外部调用状态。

    返回当前状态、阶段、进度和结果引用。
    """
    invocation = get_invocation(invocation_id, auth_ctx["tenant_id"])
    if invocation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invocation not found")

    return _to_response(invocation)


@router.post("/invocations/{invocation_id}/cancel")
async def cancel(invocation_id: str, auth_ctx: dict = Depends(verify_tageai_signature)):
    """取消外部调用。

    仅在任务尚未完成或已失败时允许取消。
    """
    invocation = cancel_invocation(invocation_id, auth_ctx["tenant_id"])
    if invocation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invocation not found")

    return _to_response(invocation)


@router.post("/callbacks", status_code=status.HTTP_200_OK)
async def callback(request: CallbackRequest, auth_ctx: dict = Depends(verify_tageai_signature)):
    """接收微信平台内部回调。

    签名验证通过后，更新调用状态。回调事件 ID 幂等：重复回调不重复处理。
    """
    if request.tenant_binding_id:
        _require_tenant_binding(request.tenant_binding_id, auth_ctx)

    logger.info(
        "Integration callback: invocationId=%s status=%s eventId=%s",
        request.invocation_id, request.status, request.event_id,
    )

    # 回调处理逻辑由 service 层实现
    from app.integrations.tageai.service import process_callback

    success = process_callback(
        invocation_id=request.invocation_id,
        tenant_id=auth_ctx["tenant_id"],
        external_job_id=request.external_job_id,
        status=request.status,
        phase=request.phase,
        progress=request.progress,
        result=request.result.model_dump() if request.result else None,
        error=request.error.model_dump() if request.error else None,
        event_id=request.event_id,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invocation not found for callback",
        )

    return {"received": True, "eventId": request.event_id}


@router.post("/connector-accounts", status_code=status.HTTP_201_CREATED)
def create_connector_account(
    request: CreateConnectorAccountRequest,
    auth_ctx: dict = Depends(verify_tageai_signature),
    db: Session = Depends(get_mysql_db),
):
    """创建或更新当前已验签租户的公众号连接。

    租户身份只来自 HMAC 客户端配置，调用方无法用请求体指定其他租户。业务层会加密保存
    AppSecret；这里返回的始终是可被 Agent 调度的脱敏摘要。
    """

    from app.integrations.tageai.connector_service import ConnectorAccountInputError, upsert_connector_account

    try:
        summary = upsert_connector_account(
            db,
            tenant_id=auth_ctx["tenant_id"],
            owner_user_id=request.owner_user_id,
            account_ref=request.connector_account_ref,
            display_name=request.display_name,
            app_id=request.app_id,
            app_secret=request.app_secret,
            delivery_modes=request.delivery_modes,
        )
        db.commit()
        return summary
    except ConnectorAccountInputError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "errorCode": exc.code,
            "message": str(exc),
            "retryable": False,
        }) from exc


@router.get("/connector-accounts")
def list_connector_accounts(
    owner_user_id: str = Query(..., alias="ownerUserId", min_length=1, max_length=20),
    auth_ctx: dict = Depends(verify_tageai_signature),
    db: Session = Depends(get_mysql_db),
):
    """列出当前已验签租户可被 TaGeAI 调度的公众号连接。"""

    from app.integrations.tageai.connector_service import list_connector_accounts as load_accounts

    return {"items": load_accounts(db, auth_ctx["tenant_id"], owner_user_id)}


@router.post("/connector-accounts/{account_ref}/disable")
def disable_connector_account(
    account_ref: str,
    owner_user_id: str = Query(..., alias="ownerUserId", min_length=1, max_length=20),
    auth_ctx: dict = Depends(verify_tageai_signature),
    db: Session = Depends(get_mysql_db),
):
    """停用连接器账号，保留加密凭据与历史调用供审计和故障排查。"""

    from app.integrations.tageai.connector_service import ConnectorAccountInputError, disable_connector_account as disable

    try:
        summary = disable(
            db, tenant_id=auth_ctx["tenant_id"], owner_user_id=owner_user_id, account_ref=account_ref,
        )
        db.commit()
        return summary
    except ConnectorAccountInputError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "errorCode": exc.code,
            "message": str(exc),
            "retryable": False,
        }) from exc


# ============================================================================
# Helpers
# ============================================================================


def _require_tenant_binding(tenant_binding_id: str, auth_ctx: dict) -> None:
    """校验请求中的租户绑定与 HMAC 客户端登记信息一致。

    请求体中的 tenantBindingId 只用于交叉校验，不用于决定实际租户；实际 tenant_id
    始终来自已验签 client_id 的服务端配置，避免跨租户伪造。
    """
    if tenant_binding_id != auth_ctx["tenant_binding_id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "errorCode": "PERMISSION_DENIED", "message": "租户绑定与服务凭据不一致",
        })


def _to_response(invocation: dict) -> dict:
    """将内部调用记录转换为 API 响应格式。"""
    resp = {
        "invocationId": invocation["invocation_id"],
        "externalJobId": invocation.get("external_job_id"),
        "status": invocation["status"],
        "phase": invocation.get("phase", "QUEUED"),
        "progress": invocation.get("progress", 0),
        "platform": invocation.get("platform"),
        "platformLabel": invocation.get("platform_label"),
        "mediaSummary": invocation.get("media_summary"),
        "estimatedRemainingSeconds": invocation.get("estimated_remaining_seconds"),
        "heartbeatAt": invocation.get("heartbeat_at"),
        "createdAt": invocation.get("created_at"),
        "startedAt": invocation.get("started_at"),
        "finishedAt": invocation.get("finished_at"),
    }

    if invocation.get("result"):
        resp["result"] = invocation["result"]
    if invocation.get("error_code"):
        resp["error"] = {
            "errorCode": invocation["error_code"],
            "message": invocation.get("error_message", ""),
            "retryable": invocation.get("retryable", False),
        }

    return resp
