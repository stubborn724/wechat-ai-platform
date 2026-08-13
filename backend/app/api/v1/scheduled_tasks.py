"""Unified scheduled task CRUD — replaces PublishPlan + ImitationTask"""

import logging
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import (
    ArticleFormatProfile,
    FeedSourceArticle,
    ScheduledTask,
    ScheduledTaskSlot,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class ArticleSlot(BaseModel):
    content_type: str = "image_text"  # image_text / video / pure_image
    publish_domain: str = "public"    # public / private


class ScheduledErpImageConfig(BaseModel):
    """定时任务的 ERP 配图策略，分类为空时从来源全部产品中随机选择。"""

    source_key: str
    commodity_category: Optional[str] = None
    repeat_after_days: int = 3
    image_count: int = 8
    selection_scope: Optional[str] = Field(
        default=None,
        max_length=128,
        description="跨公域/私域共享的 ERP 防重范围；为空时按任务隔离",
    )


class ScheduledTemplateRotationConfig(BaseModel):
    """任务级来源模板轮换配置。

    关闭时不写入轮换状态，历史任务和不需要轮换的任务继续使用单个格式模板。启用
    时由服务层再次校验模板归属，Pydantic 只负责把前端输入收敛为稳定的数据形态。
    """

    enabled: bool = False
    profile_ids: List[int] = Field(default_factory=list)
    basis: Literal["publish_day", "publish_run"] = "publish_day"
    uses_per_template: int = Field(default=1, ge=1, le=365)

    @model_validator(mode="after")
    def validate_rotation_shape(self) -> "ScheduledTemplateRotationConfig":
        """在请求边界拦截空列表、重复模板和无效轮换单位。"""

        from app.services.scheduled_template_rotation_service import (
            normalize_template_rotation_config,
        )

        normalize_template_rotation_config(self.model_dump())
        return self


class ScheduledTaskWatermarkConfig(BaseModel):
    """任务级水印快照。

    全局水印页配置适合“当前默认样式”，但不能保证某个定时任务长期稳定。该
    对象允许任务在保存时锁定文字或 Logo 的渲染参数；没有传入时保持历史兼容，
    由 Worker 回退到租户全局水印配置。
    """

    enabled: bool = True
    type: Literal["text", "logo"] = "text"
    content: Optional[str] = None
    image_key: Optional[str] = None
    font_size: int = Field(default=24, ge=8, le=96)
    position: Literal[
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
        "center",
    ] = "bottom-right"
    opacity: float = Field(default=0.9, ge=0.0, le=1.0)
    margin: int = Field(default=40, ge=0, le=512)
    scale: float = Field(default=0.15, gt=0.0, le=1.0)
    locked: bool = True

    @model_validator(mode="after")
    def validate_enabled_payload(self) -> "ScheduledTaskWatermarkConfig":
        """启用时要求对应的文字或 Logo 资源完整，避免保存后才发现无法绘制。"""

        if not self.enabled:
            return self
        if self.type == "text" and not (self.content or "").strip():
            raise ValueError("文字水印启用时必须提供 content")
        if self.type == "logo" and not (self.image_key or "").strip():
            raise ValueError("Logo 水印启用时必须提供 image_key")
        return self


class ScheduledTaskCreate(BaseModel):
    name: str
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None  # 直接关联投喂源，替代仿写池
    feed_source_id: Optional[int] = None  # 具体选中的投喂源 ID
    feed_article_ids: Optional[List[int]] = None  # 选中的文章 ID 列表
    # 为空时由投喂上下文自动解析；没有投喂源时保持历史任务链路。
    format_profile_id: Optional[int] = None
    template_rotation_config: Optional[ScheduledTemplateRotationConfig] = None
    style: Optional[str] = None       # 写作风格
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: int = -1
    publish_times: List[str]
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: int = 1
    # HTML 版式仿写默认沿用五张图的成本保护；需要更多图片时由单个任务显式提高。
    html_image_count: int = Field(default=5, ge=1, le=30)
    public_count: int = 1
    private_count: int = 0
    approval_mode: str = "auto"
    account_ids: Optional[List[int]] = None
    publish_mode: str = "draft"  # "draft" 存草稿箱, "direct" 直接发布
    publish_domain: Literal["public", "private"] = "public"  # direct 发布域
    image_source: str = "dashscope"  # 图片来源: dashscope/local
    footer_template: Optional[str] = None
    content_type: str = "article"  # article / image / video
    # 默认值必须保持旧任务行为；纯海报只能由用户在任务级明确选择。
    layout_mode: Literal["standard", "seamless_poster"] = "standard"
    enabled_image_methods: Optional[List[str]] = None  # 配图方式
    enable_watermark: bool = False
    watermark_config: Optional[ScheduledTaskWatermarkConfig] = None
    erp_image_config: Optional[ScheduledErpImageConfig] = None


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    topic: Optional[str] = None
    feed_source_ids: Optional[List[int]] = None
    feed_source_id: Optional[int] = None
    feed_article_ids: Optional[List[int]] = None
    format_profile_id: Optional[int] = None
    template_rotation_config: Optional[ScheduledTemplateRotationConfig] = None
    style: Optional[str] = None
    knowledge_base_ids: Optional[List[int]] = None
    day_of_week: Optional[int] = None
    publish_times: Optional[List[str]] = None
    article_slots: Optional[List[ArticleSlot]] = None
    articles_per_day: Optional[int] = None
    html_image_count: Optional[int] = Field(default=None, ge=1, le=30)
    public_count: Optional[int] = None
    private_count: Optional[int] = None
    approval_mode: Optional[str] = None
    account_ids: Optional[List[int]] = None
    publish_mode: Optional[str] = None
    publish_domain: Optional[Literal["public", "private"]] = None
    image_source: Optional[str] = None
    footer_template: Optional[str] = None
    content_type: Optional[str] = None
    layout_mode: Optional[Literal["standard", "seamless_poster"]] = None
    enabled_image_methods: Optional[List[str]] = None
    enable_watermark: Optional[bool] = None
    watermark_config: Optional[ScheduledTaskWatermarkConfig] = None
    erp_image_config: Optional[ScheduledErpImageConfig] = None


class SlotResponse(BaseModel):
    id: int
    sort_order: int
    content_type: str
    publish_domain: str

    model_config = {"from_attributes": True}


class ScheduledTaskResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    is_active: bool
    writing_mode: str
    topic: Optional[str] = None
    feed_source_ids: Optional[list] = None
    feed_source_id: Optional[int] = None
    feed_article_ids: Optional[list] = None
    format_profile_id: Optional[int] = None
    template_rotation_config: Optional[dict] = None
    template_rotation_version: int = 0
    style: Optional[str] = None
    knowledge_base_ids: Optional[list] = None
    day_of_week: int
    publish_times: list
    article_slots: Optional[list] = None  # legacy JSON field (read-only)
    slots: List[SlotResponse] = []  # new slot table records
    articles_per_day: int
    html_image_count: int = 5
    public_count: int
    private_count: int
    approval_mode: str
    account_ids: Optional[list] = None
    publish_mode: str = "draft"
    publish_domain: Literal["public", "private"] = "public"
    image_source: str = "dashscope"
    footer_template: Optional[str] = None
    content_type: str = "article"
    layout_mode: Literal["standard", "seamless_poster"] = "standard"
    enabled_image_methods: Optional[list] = None
    enable_watermark: bool = False
    watermark_config: Optional[dict] = None
    erp_image_config: Optional[dict] = None
    total_generated: int
    last_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduledTaskListResponse(BaseModel):
    total: int
    items: List[ScheduledTaskResponse]


class WritingStyleTemplateOption(BaseModel):
    """供运营页面选择的模板信息，不向浏览器暴露内部提示词。"""

    identifier: str
    label: str
    description: str


def build_writing_style_template_options() -> list[WritingStyleTemplateOption]:
    """把后端模板目录转换为接口安全的展示选项。"""

    from app.services.writing_style_template_service import (
        list_writing_style_templates,
    )

    return [
        WritingStyleTemplateOption(
            identifier=template.identifier,
            label=template.label,
            description=template.description,
        )
        for template in list_writing_style_templates()
    ]


def _normalize_watermark_config_for_storage(config: object) -> Optional[dict]:
    """把 API 输入收口为数据库可长期复用的任务水印快照。"""

    if config is None:
        return None
    from app.services.scheduled_task_watermark_service import (
        normalize_task_watermark_config,
    )

    raw_config = config.model_dump() if hasattr(config, "model_dump") else config
    try:
        return normalize_task_watermark_config(raw_config)
    except ValueError as exc:
        # Pydantic 已覆盖常规 HTTP 输入；这里额外保护更新流程中来自 JSON 的
        # 字典，避免脏配置进入数据库后由 Celery Worker 才发现。
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _load_task_slots(db: Session, task: ScheduledTask) -> list:
    """Load slots from ScheduledTaskSlot table, fall back to article_slots JSON."""
    slots = db.query(ScheduledTaskSlot).filter(
        ScheduledTaskSlot.task_id == task.id
    ).order_by(ScheduledTaskSlot.sort_order).all()
    if slots:
        return slots
    # Fallback: migrate legacy JSON slots
    if task.article_slots:
        for i, s in enumerate(task.article_slots):
            slot_data = s if isinstance(s, dict) else {}
            db.add(ScheduledTaskSlot(
                task_id=task.id,
                sort_order=i,
                content_type=slot_data.get("content_type", "image_text"),
                publish_domain=slot_data.get("publish_domain", "public"),
            ))
            slots.append(db.query(ScheduledTaskSlot).order_by(
                ScheduledTaskSlot.id.desc()).first())
        db.commit()
    return slots


def _validate_format_profile_binding(
    db: Session,
    *,
    tenant_id: int,
    format_profile_id: Optional[int],
) -> None:
    """校验任务只能绑定本租户启用中的格式模板。

    任务只保存外键而不复制模板内容，便于观察版本关系；模板一旦被停用，后续编辑
    不允许继续绑定，已绑定的历史任务仍能按其版本正常执行。
    """

    if format_profile_id is None:
        return
    profile = (
        db.query(ArticleFormatProfile)
        .filter(
            ArticleFormatProfile.id == format_profile_id,
            ArticleFormatProfile.tenant_id == tenant_id,
            ArticleFormatProfile.is_active == True,  # noqa: E712
        )
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=422, detail="格式模板不存在、已停用或不属于当前租户")


def _prepare_template_rotation_config(
    db: Session,
    *,
    tenant_id: int,
    raw_config: object,
) -> Optional[dict]:
    """校验任务轮换模板，并转换为数据库可长期保存的 JSON。

    API 查询时同时限制租户、启用状态和来源文章，避免用户通过提交模板 ID 把其他
    租户、已停用或独立模板放入轮换队列。解析和顺序规则由领域服务统一维护，
    本函数只处理数据库边界。
    """

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
        validate_rotation_profiles,
    )

    payload = raw_config.model_dump() if hasattr(raw_config, "model_dump") else raw_config
    config = normalize_template_rotation_config(payload)
    if config is None:
        return None

    profiles = (
        db.query(ArticleFormatProfile)
        .filter(
            ArticleFormatProfile.tenant_id == tenant_id,
            ArticleFormatProfile.is_active == True,  # noqa: E712
            ArticleFormatProfile.source_article_id.isnot(None),
            ArticleFormatProfile.id.in_(config.profile_ids),
        )
        .all()
    )
    try:
        validate_rotation_profiles(
            profiles,
            expected_profile_ids=config.profile_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config.to_storage_dict()


def _derive_rotation_feed_source_ids(
    db: Session,
    *,
    tenant_id: int,
    rotation_config: Optional[dict],
) -> Optional[list[int]]:
    """从轮换模板反查投喂源，保证仅选择模板也能形成完整仿写上下文。"""

    from app.services.scheduled_template_rotation_service import (
        normalize_template_rotation_config,
    )

    config = normalize_template_rotation_config(rotation_config)
    if config is None:
        return None

    rows = (
        db.query(ArticleFormatProfile.id, FeedSourceArticle.feed_source_id)
        .join(
            FeedSourceArticle,
            FeedSourceArticle.id == ArticleFormatProfile.source_article_id,
        )
        .filter(
            ArticleFormatProfile.tenant_id == tenant_id,
            ArticleFormatProfile.id.in_(config.profile_ids),
            ArticleFormatProfile.source_article_id.isnot(None),
            FeedSourceArticle.tenant_id == tenant_id,
        )
        .all()
    )
    source_by_profile_id = {int(profile_id): int(source_id) for profile_id, source_id in rows}
    source_ids: list[int] = []
    for profile_id in config.profile_ids:
        source_id = source_by_profile_id.get(profile_id)
        if source_id is not None and source_id not in source_ids:
            source_ids.append(source_id)
    if len(source_by_profile_id) != len(config.profile_ids):
        raise HTTPException(status_code=422, detail="轮换模板的投喂源信息不完整")
    return source_ids


def _resolve_automatic_format_profile_id(
    db: Session,
    *,
    tenant_id: int,
    feed_article_ids: Optional[List[int]],
    feed_source_id: Optional[int],
    feed_source_ids: Optional[List[int]],
) -> Optional[int]:
    """根据投喂源或具体参考文章解析任务应锁定的模板版本。

    该解析只在任务没有显式模板 ID 时使用。投喂源没有成功格式分析时返回空值，
    任务会继续走历史标准流程，不会因为自动能力暂时缺失而绑定错误版式。
    """

    from app.services.format_profile_task_binding_service import (
        find_automatic_format_profile,
    )

    profile = find_automatic_format_profile(
        db,
        tenant_id=tenant_id,
        feed_article_ids=feed_article_ids,
        feed_source_id=feed_source_id,
        feed_source_ids=feed_source_ids,
    )
    return profile.id if profile is not None else None


# --- Routes ---


@router.get(
    "/scheduled-tasks/writing-style-templates",
    response_model=List[WritingStyleTemplateOption],
)
def list_scheduled_task_writing_style_templates(
    principal: CurrentPrincipal = Depends(require_auth),
):
    """列出当前租户可选择的内置写作模板。

    当前目录为全局内置模板；保留鉴权依赖以便后续加入租户级模板时不改变接口边界。
    """

    del principal
    return build_writing_style_template_options()

@router.get("/scheduled-tasks", response_model=ScheduledTaskListResponse)
def list_scheduled_tasks(
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List all scheduled tasks with their slots."""
    items = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.tenant_id == principal.tenant_id)
        .order_by(ScheduledTask.id.desc())
        .all()
    )
    # Manually attach slots since Ticket doesn't use relationships
    result_items = []
    for item in items:
        resp = ScheduledTaskResponse.model_validate(item)
        resp.slots = _load_task_slots(db, item)
        result_items.append(resp)
    return ScheduledTaskListResponse(total=len(items), items=result_items)


@router.post("/scheduled-tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create a new scheduled task."""
    if req.day_of_week not in range(-1, 7):
        raise HTTPException(status_code=400, detail="day_of_week must be -1 (every day) or 0-6")
    template_rotation_config = _prepare_template_rotation_config(
        db,
        tenant_id=principal.tenant_id,
        raw_config=req.template_rotation_config,
    )
    rotation_feed_source_ids = _derive_rotation_feed_source_ids(
        db,
        tenant_id=principal.tenant_id,
        rotation_config=template_rotation_config,
    )
    effective_format_profile_id = req.format_profile_id
    if template_rotation_config is not None:
        # 非轮换路径仍以 format_profile_id 为准；启用轮换时保存首个模板作为手动
        # 执行和历史兼容回退，不会影响新时段按运行记录选择的实际模板。
        effective_format_profile_id = int(template_rotation_config["profile_ids"][0])
    effective_feed_source_ids = (
        rotation_feed_source_ids
        if rotation_feed_source_ids is not None
        else req.feed_source_ids or ([req.feed_source_id] if req.feed_source_id else None)
    )
    effective_feed_source_id = (
        effective_feed_source_ids[0] if rotation_feed_source_ids is not None else req.feed_source_id
    )
    # 新建任务显式开启自动绑定；历史任务不会经过创建接口，因此不会获得该开关。
    if effective_format_profile_id is None:
        effective_format_profile_id = _resolve_automatic_format_profile_id(
            db,
            tenant_id=principal.tenant_id,
            feed_article_ids=req.feed_article_ids,
            feed_source_id=req.feed_source_id,
            feed_source_ids=req.feed_source_ids or (
                [req.feed_source_id] if req.feed_source_id else None
            ),
        )
    _validate_format_profile_binding(
        db,
        tenant_id=principal.tenant_id,
        format_profile_id=effective_format_profile_id,
    )

    task = ScheduledTask(
        tenant_id=principal.tenant_id,
        name=req.name,
        writing_mode="feed" if effective_feed_source_ids else "kb" if req.knowledge_base_ids else "free",
        feed_source_ids=effective_feed_source_ids,
        # 同时保存标量投喂源和具体文章选择，确保定时执行时复用用户明确选定的文章，
        # 不会因为只保存来源列表而退化为从投喂源随机挑选其他文章。
        feed_source_id=effective_feed_source_id,
        feed_article_ids=req.feed_article_ids,
        format_profile_id=effective_format_profile_id,
        format_profile_auto_bind_enabled=True,
        template_rotation_config=template_rotation_config,
        template_rotation_version=1 if template_rotation_config is not None else 0,
        topic=req.topic,
        style=req.style,
        knowledge_base_ids=req.knowledge_base_ids,
        day_of_week=req.day_of_week,
        publish_times=req.publish_times,
        article_slots=None,  # migrated to ScheduledTaskSlot table
        articles_per_day=req.articles_per_day,
        html_image_count=req.html_image_count,
        public_count=req.public_count,
        private_count=req.private_count,
        approval_mode=req.approval_mode,
        account_ids=req.account_ids,
        publish_mode=req.publish_mode,
        publish_domain=req.publish_domain,
        image_source=req.image_source,
        footer_template=req.footer_template,
        content_type=req.content_type,
        layout_mode=req.layout_mode,
        enabled_image_methods=req.enabled_image_methods,
        enable_watermark=req.enable_watermark,
        watermark_config=(
            _normalize_watermark_config_for_storage(req.watermark_config)
            if req.watermark_config is not None
            else None
        ),
        erp_image_config=req.erp_image_config.model_dump() if req.erp_image_config else None,
        created_by=principal.user_id,
    )
    db.add(task)
    db.flush()

    # Create slot records in the new table
    if req.article_slots:
        for i, slot in enumerate(req.article_slots):
            db.add(ScheduledTaskSlot(
                task_id=task.id,
                sort_order=i,
                content_type=slot.content_type,
                publish_domain=slot.publish_domain,
            ))

    db.commit()
    db.refresh(task)
    return task


@router.put("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def update_scheduled_task(
    task_id: int,
    req: ScheduledTaskUpdate,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Update a scheduled task."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    update_data = req.model_dump(exclude_unset=True)
    if "template_rotation_config" in update_data:
        prepared_rotation_config = _prepare_template_rotation_config(
            db,
            tenant_id=principal.tenant_id,
            raw_config=update_data["template_rotation_config"],
        )
        from app.services.scheduled_template_rotation_service import (
            normalize_template_rotation_config,
        )

        previous_config = normalize_template_rotation_config(
            task.template_rotation_config
        )
        previous_payload = (
            previous_config.to_storage_dict() if previous_config is not None else None
        )
        update_data["template_rotation_config"] = prepared_rotation_config
        if prepared_rotation_config != previous_payload:
            update_data["template_rotation_version"] = (
                int(task.template_rotation_version or 0) + 1
            )
        if prepared_rotation_config is not None:
            # 配置更新从新版本的第一个排期重新开始；已排队时段保存着旧版本和模板，
            # 因而不会被这个回退模板覆盖。
            update_data["format_profile_id"] = int(
                prepared_rotation_config["profile_ids"][0]
            )
            rotation_feed_source_ids = _derive_rotation_feed_source_ids(
                db,
                tenant_id=principal.tenant_id,
                rotation_config=prepared_rotation_config,
            )
            update_data["feed_source_ids"] = rotation_feed_source_ids
            update_data["feed_source_id"] = rotation_feed_source_ids[0]
    # 前端单选投喂源时只会提交标量 ID；先把数组视图同步到同一来源，后续自动
    # 模板解析、任务展示和执行器都读取一致的上下文。
    if "feed_source_id" in update_data and "feed_source_ids" not in update_data:
        update_data["feed_source_ids"] = (
            [update_data["feed_source_id"]]
            if update_data["feed_source_id"]
            else None
        )
    source_context_changed = any(
        field in update_data
        for field in ("feed_article_ids", "feed_source_id", "feed_source_ids")
    )
    # 只有新任务模式下投喂上下文真的变化，或一个尚未绑定的新任务被保存时才
    # 自动解析。正式历史任务的开关为 False，编辑发布时间、标题或水印不会改变
    # 它的执行链路；已经绑定模板的任务也继续锁定原版本。
    from app.services.format_profile_task_binding_service import (
        allows_automatic_format_profile_binding,
    )

    automatic_binding_enabled = allows_automatic_format_profile_binding(task)
    should_auto_bind_unbound_task = (
        task.format_profile_id is None
        and "format_profile_id" in update_data
        and update_data["format_profile_id"] is None
    )
    if automatic_binding_enabled and (
        (
            source_context_changed
            and (
                "format_profile_id" not in update_data
                or update_data.get("format_profile_id") is None
            )
        )
        or should_auto_bind_unbound_task
    ):
        candidate_feed_source_id = update_data.get(
            "feed_source_id",
            task.feed_source_id,
        )
        candidate_feed_source_ids = update_data.get(
            "feed_source_ids",
            task.feed_source_ids,
        )
        if "feed_source_id" in update_data and "feed_source_ids" not in update_data:
            candidate_feed_source_ids = (
                [candidate_feed_source_id] if candidate_feed_source_id else None
            )
        automatic_profile_id = _resolve_automatic_format_profile_id(
            db,
            tenant_id=principal.tenant_id,
            feed_article_ids=update_data.get(
                "feed_article_ids",
                task.feed_article_ids,
            ),
            feed_source_id=candidate_feed_source_id,
            feed_source_ids=candidate_feed_source_ids,
        )
        update_data["format_profile_id"] = automatic_profile_id
    if "format_profile_id" in update_data:
        _validate_format_profile_binding(
            db,
            tenant_id=principal.tenant_id,
            format_profile_id=update_data["format_profile_id"],
        )
    # Handle article_slots: replace all slot records
    if "article_slots" in update_data:
        slots_data = update_data.pop("article_slots")
        if slots_data is not None:
            db.query(ScheduledTaskSlot).filter(
                ScheduledTaskSlot.task_id == task.id
            ).delete()
            for i, slot in enumerate(slots_data):
                slot_data = slot if isinstance(slot, dict) else slot.model_dump()
                db.add(ScheduledTaskSlot(
                    task_id=task.id,
                    sort_order=i,
                    content_type=slot_data.get("content_type", "image_text"),
                    publish_domain=slot_data.get("publish_domain", "public"),
                ))

    for field, value in update_data.items():
        if field == "erp_image_config" and value is not None:
            value = value.model_dump() if hasattr(value, "model_dump") else value
        if field == "watermark_config":
            value = _normalize_watermark_config_for_storage(value)
        setattr(task, field, value)

    # auto-derive writing_mode from the presence of feed/kb sources
    if "feed_source_ids" in update_data or "feed_source_id" in update_data or "knowledge_base_ids" in update_data:
        task.writing_mode = "feed" if (task.feed_source_ids or task.feed_source_id) else "kb" if task.knowledge_base_ids else "free"
    # 前端只传 feed_source_id 时不一定会更新 feed_source_ids，这里补上
    if "feed_source_id" in update_data and not task.feed_source_ids:
        task.feed_source_ids = [update_data["feed_source_id"]] if update_data["feed_source_id"] else None

    db.commit()
    db.refresh(task)
    return task


@router.delete("/scheduled-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scheduled_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete a scheduled task."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    db.delete(task)
    db.commit()


@router.post("/scheduled-tasks/{task_id}/toggle", response_model=ScheduledTaskResponse)
def toggle_scheduled_task(
    task_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Toggle task active/inactive."""
    task = db.query(ScheduledTask).filter(
        ScheduledTask.id == task_id,
        ScheduledTask.tenant_id == principal.tenant_id,
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    task.is_active = not task.is_active
    db.commit()
    db.refresh(task)
    return task
