from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, JSON, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.database import MysqlBase


class Tenant(MysqlBase):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(128), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    users = relationship("Membership", back_populates="tenant", viewonly=True)


class User(MysqlBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    memberships = relationship("Membership", back_populates="user", viewonly=True)


class Membership(MysqlBase):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(64), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    tenant = relationship("Tenant", back_populates="users")
    user = relationship("User", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )


class RefreshToken(MysqlBase):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    family_id = Column(String(64), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    replaced_by_id = Column(Integer, ForeignKey("refresh_tokens.id"), nullable=True)

    __table_args__ = (
        Index("ix_refresh_tokens_token_hash", token_hash),
    )


class WeChatAccount(MysqlBase):
    __tablename__ = "wechat_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    app_id = Column(String(128), nullable=False)
    auth_mode = Column(String(64), nullable=False)
    status = Column(String(64), nullable=False, default="active")
    capabilities = Column(JSON, nullable=True)
    last_health_at = Column(DateTime, nullable=True)
    last_health_error = Column(Text, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_wechat_accounts_tenant_app", "tenant_id", "app_id"),
    )


class AccountCredential(MysqlBase):
    __tablename__ = "account_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    encrypted_secret = Column(Text, nullable=False)
    key_version = Column(String(64), nullable=False)


class Article(MysqlBase):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    topic = Column(Text, nullable=True)
    style = Column(String(64), nullable=True)
    main_title = Column(String(255), nullable=True)
    sub_title = Column(String(255), nullable=True)
    title_options = Column(JSON, nullable=True)
    outline = Column(JSON, nullable=True)
    content = Column(Text, nullable=True)
    full_content = Column(Text, nullable=True)
    cover_image = Column(String(512), nullable=True)
    images = Column(JSON, nullable=True)
    footer_template = Column(Text, nullable=True)
    status = Column(String(64), nullable=False, default="pending")
    phase = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AgentLog(MysqlBase):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("articles.task_id"), nullable=False, index=True)
    agent_name = Column(String(128), nullable=False)
    status = Column(String(64), nullable=False)
    prompt = Column(Text, nullable=True)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)


class ContentJob(MysqlBase):
    __tablename__ = "content_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True)
    status = Column(String(64), nullable=False, default="pending")
    version = Column(Integer, nullable=False, default=1)
    topic = Column(String(255), nullable=False)
    content_type = Column(String(64), nullable=False, default="article")
    approval_mode = Column(String(64), nullable=False, default="auto")
    scheduled_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    generation_config = Column(JSON, nullable=True)
    footer_template = Column(Text, nullable=True)
    signature_config = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_content_jobs_tenant_status", "tenant_id", "status"),
    )


class ContentVersion(MysqlBase):
    __tablename__ = "content_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("content_jobs.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=True)
    body_markdown = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    citations = Column(JSON, nullable=True)
    findings = Column(JSON, nullable=True)
    model_metadata = Column(JSON, nullable=True)
    source = Column(String(64), nullable=True)
    cover_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True)
    article_content_type = Column(String(64), nullable=True)
    publish_domain = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "version_number", name="uq_content_version_job_version"),
        Index("ix_content_versions_tenant", "tenant_id"),
    )


class Review(MysqlBase):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("content_jobs.id"), nullable=False)
    content_version_id = Column(Integer, ForeignKey("content_versions.id"), nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(String(64), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_reviews_job", "job_id"),
    )


class PublishAttempt(MysqlBase):
    __tablename__ = "publish_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("content_jobs.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    idempotency_key = Column(String(128), nullable=False, index=True)
    mode = Column(String(64), nullable=False)
    status = Column(String(64), nullable=False, default="pending")
    platform_media_id = Column(String(255), nullable=True)
    platform_message_id = Column(String(255), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_publish_attempts_tenant_job", "tenant_id", "job_id"),
    )


class PublishSchedule(MysqlBase):
    __tablename__ = "publish_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("content_jobs.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    missed_policy = Column(String(64), nullable=False, default="skip")
    status = Column(String(64), nullable=False, default="pending")
    dispatched_at = Column(DateTime, nullable=True)
    task_id = Column(String(128), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_publish_schedules_tenant_status", "tenant_id", "status"),
        Index("ix_publish_schedules_scheduled", "scheduled_at"),
    )


class PublishPlan(MysqlBase):
    __tablename__ = "publish_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    article_slots = Column(JSON, nullable=True)
    publish_times = Column(JSON, nullable=True)
    public_count = Column(Integer, nullable=False, default=0)
    private_count = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_publish_plans_tenant_account", "tenant_id", "account_id"),
    )


class FeedSource(MysqlBase):
    __tablename__ = "feed_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    slug = Column(String(128), nullable=False, index=True)
    source_type = Column(String(64), nullable=False)
    source_identifier = Column(String(255), nullable=False)
    feed_url = Column(String(512), nullable=True)
    status = Column(String(64), nullable=False, default="active")
    style_profile = Column(JSON, nullable=True)
    last_fetched_at = Column(DateTime, nullable=True)
    fetch_interval_minutes = Column(Integer, nullable=True, default=60)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_feed_source_tenant_slug"),
    )


class FeedSourceArticle(MysqlBase):
    __tablename__ = "feed_source_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id"), nullable=False)
    title = Column(String(255), nullable=True)
    article_url = Column(String(512), nullable=True)
    body_markdown = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    cover_image_url = Column(String(512), nullable=True)
    published_at = Column(DateTime, nullable=True)
    word_count = Column(Integer, nullable=True)
    is_analyzed = Column(Boolean, default=False, nullable=False)
    analysis = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_feed_articles_source", "feed_source_id"),
        Index("ix_feed_articles_tenant", "tenant_id"),
    )


class ContentJobArticle(MysqlBase):
    __tablename__ = "content_job_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("content_jobs.id"), nullable=False)
    content_type = Column(String(64), nullable=False, default="article")
    sort_order = Column(Integer, nullable=False, default=0)
    topic_override = Column(String(255), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    publish_domain = Column(String(255), nullable=True)
    status = Column(String(64), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_content_job_articles_job", "job_id"),
        Index("ix_content_job_articles_tenant", "tenant_id"),
    )


class Asset(MysqlBase):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=True)
    asset_type = Column(String(64), nullable=False)
    mime_type = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=True)
    storage_key = Column(String(512), nullable=False)
    thumbnail_key = Column(String(512), nullable=True)
    tags = Column(JSON, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    is_watermarked = Column(Boolean, default=False, nullable=False)
    watermark_config = Column(JSON, nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_assets_tenant_type", "tenant_id", "asset_type"),
    )


class AssetVariant(MysqlBase):
    __tablename__ = "asset_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    variant_type = Column(String(64), nullable=False)
    storage_key = Column(String(512), nullable=False)
    file_size = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)

    __table_args__ = (
        Index("ix_asset_variants_asset_type", "asset_id", "variant_type", unique=True),
    )


class AssetUsage(MysqlBase):
    __tablename__ = "asset_usages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    content_version_id = Column(Integer, ForeignKey("content_versions.id"), nullable=True)
    usage_type = Column(String(64), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_asset_usages_asset", "asset_id"),
    )


# ============================================================
# 阶段一：微信开放平台扫码授权
# ============================================================


class WeChatOAuthAccount(MysqlBase):
    """通过微信开放平台扫码授权的公众号"""
    __tablename__ = "wechat_oauth_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    # 授权信息
    app_id = Column(String(128), nullable=False, comment="授权公众号的 AppID")
    nick_name = Column(String(255), nullable=True, comment="公众号昵称")
    head_img = Column(String(512), nullable=True, comment="公众号头像 URL")
    service_type_info = Column(Integer, nullable=True, comment="公众号类型")
    verify_type_info = Column(Integer, nullable=True, comment="认证类型")
    user_name = Column(String(128), nullable=True, comment="原始 ID")
    alias = Column(String(255), nullable=True, comment="微信号")
    qrcode_url = Column(String(512), nullable=True, comment="二维码 URL")
    business_info = Column(JSON, nullable=True)
    # Token
    authorizer_access_token = Column(Text, nullable=True)
    authorizer_refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    func_info = Column(JSON, nullable=True, comment="授权给第三方平台的权限集")
    # 状态
    is_active = Column(Boolean, default=True, nullable=False)
    authorization_app_id = Column(String(128), nullable=True, comment="第三方平台 app_id")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "app_id", name="uq_oauth_account_tenant_app"),
    )


# ============================================================
# 阶段二：多源仿写
# ============================================================


class ImitationPool(MysqlBase):
    """仿写池 — 一组要仿写的公众号/Feed 源"""
    __tablename__ = "imitation_pools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False, comment="仿写池名称")
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ImitationPoolSource(MysqlBase):
    """仿写池中的来源 — 关联 FeedSource 或直接录入公众号"""
    __tablename__ = "imitation_pool_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_id = Column(Integer, ForeignKey("imitation_pools.id", ondelete="CASCADE"), nullable=False, index=True)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id", ondelete="SET NULL"), nullable=True)
    # 如果是直接录入的公众号（非 FeedSource）
    wechat_name = Column(String(255), nullable=True, comment="公众号名称")
    wechat_app_id = Column(String(128), nullable=True, comment="公众号 AppID")
    wechat_original_id = Column(String(128), nullable=True, comment="原始 ID")
    weight = Column(Integer, nullable=False, default=1, comment="权重，越高被选中概率越大")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class ImitationTask(MysqlBase):
    """仿写任务 — 配置每天仿写多少篇、什么策略"""
    __tablename__ = "imitation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    pool_id = Column(Integer, ForeignKey("imitation_pools.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False, comment="任务名称")
    # 任务策略
    strategy = Column(String(64), nullable=False, default="random",
                      comment="仿写策略: random=随机选源, round_robin=轮流, exhaust=全部仿写完")
    articles_per_day = Column(Integer, nullable=False, default=1, comment="每天仿写篇数")
    content_types = Column(JSON, nullable=True, comment="内容类型列表，如 [\"article\"], [\"article\",\"image\"]")
    # 排期
    start_date = Column(DateTime, nullable=True, comment="开始日期")
    end_date = Column(DateTime, nullable=True, comment="结束日期")
    publish_times = Column(JSON, nullable=True, comment="每天发布时间列表，如 [\"09:00\", \"14:00\"]")
    # 关联账号
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True, comment="发布目标公众号")
    approval_mode = Column(String(64), nullable=False, default="auto", comment="auto/manual")
    # 知识库和素材
    knowledge_base_ids = Column(JSON, nullable=True, comment="关联的知识库 ID 列表")
    footer_template = Column(Text, nullable=True, comment="底部模板")
    # 状态
    status = Column(String(64), nullable=False, default="active", comment="active/paused/completed")
    total_generated = Column(Integer, nullable=False, default=0, comment="累计生成篇数")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_imitation_tasks_tenant", "tenant_id"),
        Index("ix_imitation_tasks_status", "status"),
    )


class ImitationTaskResult(MysqlBase):
    """仿写任务执行结果 — 每次仿写生成的文章记录"""
    __tablename__ = "imitation_task_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("imitation_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    pool_source_id = Column(Integer, ForeignKey("imitation_pool_sources.id", ondelete="SET NULL"), nullable=True)
    # 生成的版本
    content_version_id = Column(Integer, ForeignKey("content_versions.id", ondelete="SET NULL"), nullable=True)
    content_job_id = Column(Integer, ForeignKey("content_jobs.id", ondelete="SET NULL"), nullable=True)
    # 当时引用的来源信息（冗余，防止源被删除）
    source_name = Column(String(255), nullable=True, comment="仿写来源名称")
    structure_analysis = Column(JSON, nullable=True, comment="当时的结构分析结果")
    # 状态
    status = Column(String(64), nullable=False, default="generated", comment="generated/published/failed")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AuditLog(MysqlBase):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(128), nullable=False)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(128), nullable=True)
    request_id = Column(String(64), nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_audit_logs_tenant_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_created", "created_at"),
    )


class Notification(MysqlBase):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read_at"),
    )
