from sqlalchemy import Column, Integer, String, DateTime, Date, Boolean, Text, JSON, ForeignKey, Index, UniqueConstraint, func, Float
from sqlalchemy.dialects.mysql import MEDIUMTEXT
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
    callback_key = Column(String(64), nullable=True, unique=True, comment="回调URL中使用的不可枚举标识")
    callback_token = Column(String(128), nullable=True, comment="微信回调验证token")
    callback_aes_key = Column(String(256), nullable=True, comment="微信回调AESKey（安全模式）")
    last_health_at = Column(DateTime, nullable=True)
    last_health_error = Column(Text, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_wechat_accounts_tenant_app", "tenant_id", "app_id"),
        Index("ix_wechat_accounts_callback_key", "callback_key"),
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
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    task_id = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    topic = Column(Text, nullable=True)
    style = Column(String(64), nullable=True)
    main_title = Column(String(255), nullable=True)
    sub_title = Column(String(255), nullable=True)
    title_options = Column(JSON, nullable=True)
    outline = Column(JSON, nullable=True)
    # 公众号 HTML 会携带大量内联样式、节点属性和图片 URL，普通 TEXT 的 64 KiB
    # 上限不足以覆盖 19 张图的版式文章；两个字段必须保持同一容量，避免先写入
    # content 成功、再写入 full_content 失败，造成文章状态与发布流程不一致。
    content = Column(MEDIUMTEXT, nullable=True)
    full_content = Column(MEDIUMTEXT, nullable=True)
    cover_image = Column(String(512), nullable=True)
    images = Column(JSON, nullable=True)
    footer_template = Column(Text, nullable=True)
    msg_data_id = Column(String(128), nullable=True, comment="微信发布后返回的 msg_data_id")
    publish_id = Column(String(128), nullable=True, comment="微信发布任务 ID")
    status = Column(String(64), nullable=False, default="pending")
    phase = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    # 微信发布信息
    wechat_account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True, index=True)
    wechat_publish_time = Column(DateTime, nullable=True)

    # 阅读指标缓存（最新值，历史存 ArticleMetrics）
    latest_read_count = Column(Integer, default=0)
    latest_like_count = Column(Integer, default=0)
    latest_share_count = Column(Integer, default=0)
    latest_comment_count = Column(Integer, default=0)
    latest_fav_count = Column(Integer, default=0)
    metrics_updated_at = Column(DateTime, nullable=True)

    # 质量评分缓存（最新值，历史存 ArticleQualityEvaluation）
    latest_quality_score = Column(Integer, nullable=True)
    quality_evaluated_at = Column(DateTime, nullable=True)

    # 优化版本关系
    source_article_id = Column(Integer, ForeignKey("articles.id"), nullable=True, index=True)
    optimization_generation = Column(Integer, default=0)
    optimization_status = Column(String(32), nullable=True)
    manual_optimization_disabled = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_articles_tenant_status", "tenant_id", "status"),
    )


class ArticleMetrics(MysqlBase):
    """阅读指标时序表 — 每日快照"""
    __tablename__ = "article_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    wechat_account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True)
    metric_date = Column(Date, nullable=False)
    fetched_at = Column(DateTime, nullable=False, server_default=func.now())

    read_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    add_to_fav_count = Column(Integer, default=0)
    exposure_count = Column(Integer, nullable=True)
    read_user_count = Column(Integer, nullable=True)

    raw_payload = Column(JSON, nullable=True)
    sync_status = Column(String(32), default="pending")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("article_id", "metric_date", name="uq_article_metrics_date"),
        Index("ix_article_metrics_date", "metric_date"),
    )


class ArticleQualityEvaluation(MysqlBase):
    """AI 质量评分记录表"""
    __tablename__ = "article_quality_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)

    content_score = Column(Integer, nullable=True)
    readability_score = Column(Integer, nullable=True)
    structure_score = Column(Integer, nullable=True)
    value_score = Column(Integer, nullable=True)
    title_score = Column(Integer, nullable=True)
    title_consistency_score = Column(Integer, nullable=True)
    credibility_score = Column(Integer, nullable=True)
    overall_score = Column(Integer, nullable=True)

    issues = Column(JSON, nullable=True)
    suggestions = Column(JSON, nullable=True)
    rewrite_recommended = Column(Boolean, default=False)
    rewrite_scope = Column(String(64), nullable=True)
    factual_risk = Column(String(32), nullable=True)
    brand_risk = Column(String(32), nullable=True)
    confidence = Column(Float, nullable=True)

    model_name = Column(String(128), nullable=False)
    model_version = Column(String(64), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    input_content_hash = Column(String(64), nullable=True, index=True)
    raw_response = Column(JSON, nullable=True)

    status = Column(String(32), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_quality_eval_article", "article_id", "status"),
    )


class ArticleOptimization(MysqlBase):
    """文章优化记录表"""
    __tablename__ = "article_optimizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    source_article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    optimized_article_id = Column(Integer, ForeignKey("articles.id"), nullable=True)

    trigger_type = Column(String(32), nullable=False, default="auto")
    trigger_evaluation_id = Column(Integer, nullable=True)
    optimization_type = Column(String(64), nullable=False)
    optimization_generation = Column(Integer, default=1)
    optimization_instruction = Column(Text, nullable=True)

    model_name = Column(String(128), nullable=True)
    model_version = Column(String(64), nullable=True)
    prompt_version = Column(String(64), nullable=True)
    change_summary = Column(Text, nullable=True)

    status = Column(String(32), nullable=False, default="created")
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, nullable=True)

    published_at = Column(DateTime, nullable=True)
    comparison_result = Column(String(32), nullable=True)
    comparison_summary = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_article_optimizations_status", "status"),
    )


class TaskExecutionLog(MysqlBase):
    """自动任务执行日志"""
    __tablename__ = "task_execution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(128), nullable=False, index=True)
    task_id = Column(String(128), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True)

    status = Column(String(32), nullable=False, default="running")
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    extra_data = Column(JSON, nullable=True, comment="任务附加数据（原 metadata，因 SQLAlchemy 保留字改名）")

    __table_args__ = (
        Index("ix_task_log_name_status", "task_name", "status"),
    )


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
    body_markdown = Column(MEDIUMTEXT, nullable=True)
    body_html = Column(MEDIUMTEXT, nullable=True)
    summary = Column(MEDIUMTEXT, nullable=True)
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
# 内容素材 — 图片/视频的生成素材存这里，不塞 Article 表
# ============================================================


class ContentAsset(MysqlBase):
    """内容素材：纯图片/视频任务产生的各类中间和最终素材"""
    __tablename__ = "content_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("content_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(32), nullable=False, default="image", comment="image / video")
    asset_type = Column(String(64), nullable=False, comment="background_image / final_image / storyboard_image / audio / subtitle / video / cover / script")
    storage_key = Column(String(512), nullable=False, comment="MinIO 对象存储 key")
    file_format = Column(String(16), nullable=True, comment="jpg / png / mp3 / mp4 / srt")
    file_size = Column(Integer, nullable=True, comment="字节数")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_sec = Column(Integer, nullable=True, comment="音频/视频时长（秒）")
    sort_order = Column(Integer, nullable=False, default=0, comment="分镜序号等排序")
    version = Column(Integer, nullable=False, default=1, comment="版本号")
    phase = Column(String(64), nullable=False, default="pending", comment="生成阶段: pending/generating/completed/failed")
    error_message = Column(Text, nullable=True)
    generation_config = Column(JSON, nullable=True, comment="生成参数快照")
    parent_asset_id = Column(Integer, ForeignKey("content_assets.id", ondelete="SET NULL"), nullable=True, comment="父版本 ID，用于版本链")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_content_assets_job_type", "job_id", "asset_type"),
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
    title = Column(String(255), nullable=True, comment="用户指定的标题（不为空则直接使用，不仿写标题）")
    # 默认继续执行历史内容仿写。只有用户显式选择 html_layout 时，生成服务才会
    # 把参考文章的原始 DOM 交给槽位流水线，避免升级后悄悄改变已有任务的版式。
    imitation_mode = Column(
        String(32),
        nullable=False,
        default="content",
        server_default="content",
        comment="仿写模式: content=内容结构, html_layout=保留HTML版式",
    )
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


# ============================================================
# 阶段三：评论管理 & 私信
# ============================================================


class WeChatComment(MysqlBase):
    """微信公众号文章评论 — 同步微信后台评论到本地"""
    __tablename__ = "wechat_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, nullable=True, comment="授权公众号 ID")
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True, comment="所属文章")
    # 微信侧数据
    msg_id = Column(String(128), nullable=False, comment="文章 msg_id（群发返回的）")
    comment_id = Column(String(128), nullable=False, index=True, comment="微信评论 ID（comment_id）")
    user_comment_id = Column(String(128), nullable=True, comment="微信用户评论 ID（user_comment_id）")
    openid = Column(String(128), nullable=True, comment="评论用户的 OpenID")
    nickname = Column(String(255), nullable=True, comment="评论用户昵称")
    content = Column(Text, nullable=False, comment="评论内容")
    create_time = Column(DateTime, nullable=True, comment="评论时间")
    # 回复
    reply_content = Column(Text, nullable=True, comment="运营回复内容")
    reply_create_time = Column(DateTime, nullable=True, comment="回复时间")
    # 状态
    is_favorited = Column(Boolean, default=False, nullable=False, comment="是否精选")
    status = Column(String(32), nullable=False, default="pending", comment="pending/replied/ignored")
    # 本地元信息
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True, comment="分配给谁回复")
    remark = Column(Text, nullable=True, comment="内部备注")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_wechat_comments_tenant", "tenant_id"),
        Index("ix_wechat_comments_account", "account_id"),
        Index("ix_wechat_comments_article", "article_id"),
        Index("ix_wechat_comments_status", "status"),
        UniqueConstraint("account_id", "comment_id", name="uq_comment_per_account"),
    )


class WeChatMessage(MysqlBase):
    """公众号主动私信记录"""
    __tablename__ = "wechat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, nullable=True, comment="发送公众号")
    openid = Column(String(128), nullable=False, index=True, comment="目标用户 OpenID")
    msg_type = Column(String(32), nullable=False, comment="text/image/video/voice/miniprogrampage")
    content = Column(Text, nullable=True, comment="文本内容（文本消息）")
    media_id = Column(String(128), nullable=True, comment="素材 media_id（图片/视频消息）")
    media_url = Column(String(512), nullable=True, comment="素材 URL")
    # 小程序卡片专用
    mini_title = Column(String(255), nullable=True, comment="小程序卡片标题")
    mini_page_path = Column(String(512), nullable=True, comment="小程序页面路径")
    mini_app_id = Column(String(128), nullable=True, comment="小程序 app_id")
    # 状态
    status = Column(String(32), nullable=False, default="sent", comment="sent/failed")
    error_message = Column(Text, nullable=True, comment="发送失败原因")
    sent_at = Column(DateTime, nullable=True, comment="发送时间")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_wechat_messages_tenant", "tenant_id"),
        Index("ix_wechat_messages_account", "account_id"),
    )


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


class ScheduledTask(MysqlBase):
    """统一的定时任务 — 取代 PublishPlan + ImitationTask"""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # 基本
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # 写作来源: free / feed / kb
    writing_mode = Column(String(32), nullable=False, default="free")
    topic = Column(Text, nullable=True)
    style = Column(String(64), nullable=True)

    # 投喂源配置（直接引用投喂源，替代仿写池）
    feed_source_ids = Column(JSON, nullable=True)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id"), nullable=True, comment="具体选中的投喂源（用于选文章）")
    feed_article_ids = Column(JSON, nullable=True, comment="选中投喂源中的具体文章 ID 列表")

    # 知识库
    knowledge_base_ids = Column(JSON, nullable=True)

    # 日程: -1=每天, 0-6=周几
    day_of_week = Column(Integer, default=-1)
    publish_times = Column(JSON, nullable=False)

    # 内容配置
    article_slots = Column(JSON, nullable=True)
    articles_per_day = Column(Integer, default=1)
    # HTML 版式任务每篇最多生成的图片数量。默认五张保护历史任务成本，
    # 只有用户明确配置时才扩大，且最终仍由 HTML 槽位选择器限制在实际图片数内。
    html_image_count = Column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
        comment="HTML仿写每篇生成图片数量，默认5，范围1-30",
    )
    public_count = Column(Integer, default=1)
    private_count = Column(Integer, default=0)

    # 发布配置
    approval_mode = Column(String(32), default="auto")
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True)
    account_ids = Column(JSON, nullable=True, comment="多选公众号 ID 列表，优先级高于 account_id")
    publish_mode = Column(String(32), default="draft", comment="draft=存草稿箱, direct=直接发布")
    image_source = Column(String(64), default="dashscope", comment="图片来源: dashscope/local")
    footer_template = Column(Text, nullable=True)
    content_type = Column(String(32), default="article", comment="article/image/video")
    # 配图方式列表（JSON 数组）
    enabled_image_methods = Column(JSON, nullable=True, comment="配图方式列表")
    enable_watermark = Column(Boolean, default=False, comment="是否启用图片水印")
    # ERP 分类配图策略。使用 JSON 是为了让旧任务保持兼容，并允许后续扩展筛选条件。
    erp_image_config = Column(JSON, nullable=True, comment="ERP 分类随机配图与防重配置")

    # 仿写（用于 writing_mode=imitation）
    pool_id = Column(Integer, ForeignKey("imitation_pools.id"), nullable=True)
    strategy = Column(String(32), nullable=True, comment="random / round_robin / exhaust")

    # 统计
    total_generated = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_scheduled_tasks_tenant", "tenant_id"),
    )


class ScheduledTaskSlot(MysqlBase):
    """定时任务的文章槽配置 — 替代 ScheduledTask.article_slots JSON"""
    __tablename__ = "scheduled_task_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    content_type = Column(String(32), nullable=False, default="image_text", comment="image_text / video / pure_image")
    publish_domain = Column(String(32), nullable=False, default="public", comment="public / private")


class ScheduledTaskRun(MysqlBase):
    """定时任务单个时间点的执行记录。

    任务的 ``last_run_at`` 只能表达最近一次运行，无法区分同日多个发布时间。
    该表以任务、日期和计划时间唯一化，使多实例轮询下也不会重复创建同一时段任务；
    尝试次数和下次重试时间持久化后，Worker 重启也能恢复中断执行，而不是让记录
    永久停留在 ``running``。
    """

    __tablename__ = "scheduled_task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_date = Column(Date, nullable=False)
    scheduled_time = Column(String(5), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        comment="queued/running/retrying/completed/failed",
    )
    article_id = Column(Integer, ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)
    error_message = Column(Text, nullable=True)
    # 记录实际执行尝试次数，而不是只依赖 Celery 内存中的 retries；这样 Worker
    # 丢失后由定时补偿重新派发时仍有明确上限，避免数据库状态驱动无限重试。
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="当前执行尝试次数，包含初次执行",
    )
    # Celery 重试是异步的，持久化时间便于调度器和前端解释“下一次何时再试”。
    next_retry_at = Column(DateTime, nullable=True, comment="下一次允许重试的时间")
    # 用于区分 Worker 丢失后同一消息的安全重投与并发重复消息。
    celery_task_id = Column(String(255), nullable=True, comment="当前 Celery 消息 ID")
    # 多公众号发布不能只用 Article.wechat_account_id 覆盖记录；此字段按
    # ``article_id:account_id`` 保存每个外部交付结果，重试时只补发未成功账号。
    delivery_results = Column(JSON, nullable=True, comment="按文章和公众号记录外部交付结果")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("task_id", "scheduled_date", "scheduled_time", name="uq_scheduled_task_run_slot"),
        Index("ix_scheduled_task_runs_task_date", "task_id", "scheduled_date"),
        Index("ix_scheduled_task_runs_recovery", "status", "next_retry_at"),
    )


class ScheduledTaskErpImageUsage(MysqlBase):
    """ERP 分类图片使用历史。

    以 ERP 原始图片 URL 作为防重键，避免同一图片被重复导入或因产品名称变化逃过
    防重规则；本地素材 ID 仅用于追溯已归档文件。
    """

    __tablename__ = "scheduled_task_erp_image_usages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(Integer, ForeignKey("scheduled_task_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    erp_image_url = Column(String(2048), nullable=False)
    product_name = Column(String(255), nullable=False)
    used_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_scheduled_task_erp_image_window", "task_id", "used_at"),
    )


class TenantWatermarkConfig(MysqlBase):
    """租户级水印配置"""
    __tablename__ = "tenant_watermark_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, unique=True, index=True)

    # 开关
    enabled = Column(Boolean, default=False, nullable=False)

    # 水印类型: logo / text
    watermark_type = Column(String(32), default="logo", nullable=False)

    # Logo 模式
    logo_image_key = Column(String(512), nullable=True, comment="MinIO 中 Logo 图片的 storage_key")
    logo_url = Column(String(512), nullable=True, comment="Logo 图片可访问 URL")
    scale = Column(Integer, default=15, nullable=False, comment="Logo 缩放比例（百分比）")

    # 文字模式
    text_content = Column(String(255), nullable=True, comment="文字水印内容")
    font_size = Column(Integer, default=36, nullable=False)

    # 通用
    position = Column(String(32), default="bottom-right", nullable=False,
                       comment="top-left / top-right / bottom-left / bottom-right / center")
    opacity = Column(Integer, default=80, nullable=False, comment="透明度 0-100")
    color = Column(String(32), default="#FFFFFF", nullable=False, comment="文字颜色 HEX")
    margin = Column(Integer, default=20, nullable=False, comment="边距 px")

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_tenant_watermark_tenant", "tenant_id"),
    )


class WeChatSyncedArticle(MysqlBase):
    """从微信同步的文章索引（草稿箱 + 已发布），正文按需实时拉取"""
    __tablename__ = "wechat_synced_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)

    # 微信侧标识
    article_type = Column(String(16), nullable=False, comment="draft / published")
    media_id = Column(String(128), nullable=True, comment="草稿 media_id（仅草稿箱）")
    wechat_article_id = Column(String(128), nullable=True, comment="已发布文章的 article_id")

    # 文章信息
    title = Column(String(255), nullable=True)
    author = Column(String(64), nullable=True)
    digest = Column(Text, nullable=True)
    cover_url = Column(String(512), nullable=True)
    wechat_url = Column(String(512), nullable=True, comment="已发布文章的永久链接")
    content = Column(Text, nullable=True, comment="缓存正文（可选）")
    publish_time = Column(DateTime, nullable=True, comment="微信端发布时间")

    # 同步状态
    is_deleted = Column(Boolean, default=False, nullable=False, comment="微信端已删除")
    need_open_comment = Column(Integer, default=0, nullable=False, comment="1=已开启评论")
    msg_data_id = Column(String(128), nullable=True, comment="评论 API 用的 msg_data_id（从 URL mid 提取）")
    raw_data = Column(JSON, nullable=True, comment="微信原始返回数据")
    last_synced_at = Column(DateTime, nullable=False, comment="最近同步时间")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ws_articles_account_type", "account_id", "article_type"),
        Index("ix_ws_articles_tenant", "tenant_id"),
        UniqueConstraint("account_id", "article_type", "media_id", "wechat_article_id",
                         name="uq_ws_article_identity"),
    )


class WeChatCommentAutoConfig(MysqlBase):
    """评论自动回复 & 自动私信配置 — 按公众号独立设置"""
    __tablename__ = "wechat_comment_auto_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False, unique=True, index=True)

    # 自动回复
    auto_reply_enabled = Column(Boolean, default=False, nullable=False, comment="是否开启自动回复")
    auto_reply_content = Column(Text, nullable=True, comment="自动回复内容")

    # 自动私信
    auto_msg_enabled = Column(Boolean, default=False, nullable=False, comment="是否开启自动私信")
    auto_msg_content = Column(Text, nullable=True, comment="自动私信内容")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CommentLead(MysqlBase):
    """评论线索 — 评论到私域转化的核心实体"""
    __tablename__ = "comment_leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    comment_id = Column(Integer, ForeignKey("wechat_comments.id"), nullable=False)
    openid = Column(String(128), nullable=False, comment="评论用户 OpenID")

    # 意图分析（只存 comment_leads，不写 wechat_comments）
    intent_type = Column(String(32), nullable=True, comment="purchase/price/cooperation/after_sale/interaction/spam")
    intent_score = Column(Integer, nullable=True, comment="置信度 0-100")
    intent_analyzed_at = Column(DateTime, nullable=True)

    # 公开回复
    reply_type = Column(String(16), nullable=True, comment="normal/guide")
    reply_content = Column(Text, nullable=True, comment="公开回复内容")
    replied_at = Column(DateTime, nullable=True)

    # 私信资格缓存
    eligibility_cache = Column(JSON, nullable=True, comment="{eligible, reason_code, reason_text, ...}")

    # 三态资格（P1.3）
    eligibility_status = Column(String(16), nullable=True, comment="eligible/ineligible/unknown")
    eligibility_reason_code = Column(String(64), nullable=True)
    eligibility_reason_text = Column(String(255), nullable=True)
    eligibility_recommended_action = Column(String(64), nullable=True)
    eligibility_checked_at = Column(DateTime, nullable=True)
    eligibility_expires_at = Column(DateTime, nullable=True)
    eligibility_source = Column(String(32), nullable=True, comment="interaction_cache/wechat_api/fallback")

    # P2 引导关键词
    guide_keyword = Column(String(128), nullable=True, comment="引导回复关键词（原文）")
    guide_keyword_normalized = Column(String(128), nullable=True, comment="引导关键词（标准化）")
    guide_sent_at = Column(DateTime, nullable=True, comment="引导回复发送时间")
    auto_send_on_message = Column(Boolean, default=False, nullable=False, comment="用户发送消息后自动发送资料")
    auto_send_package_id = Column(Integer, nullable=True, comment="自动发送的资料包ID")

    # 跟进状态
    status = Column(String(32), nullable=False, default="pending_reply",
                    comment="pending_reply/awaiting_user/eligible/contact_sent/converted/closed/failed/manual_review")

    # 分配
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    remark = Column(Text, nullable=True)

    # 关联资料包
    contact_package_id = Column(Integer, nullable=True)

    last_action_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "comment_id", name="uq_lead_comment_per_account"),
        Index("ix_lead_tenant_status", "tenant_id", "status"),
        Index("ix_lead_account_openid", "account_id", "openid"),
        Index("ix_lead_assigned", "assigned_to"),
    )


class SyncJob(MysqlBase):
    """异步同步任务状态"""
    __tablename__ = "sync_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, nullable=False, comment="同步的公众号 ID")
    job_type = Column(String(32), nullable=False, default="sync_comments", comment="任务类型")
    scope = Column(String(32), nullable=False, default="all", comment="all/article")
    article_id = Column(Integer, nullable=True, comment="单篇文章 ID（scope=article）")
    status = Column(String(16), nullable=False, default="pending", comment="pending/running/completed/failed")
    result = Column(JSON, nullable=True, comment="完成结果 {new_leads, synced_articles, ...}")
    error_message = Column(Text, nullable=True)
    celery_task_id = Column(String(128), nullable=True, comment="Celery 任务 ID")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_sync_jobs_tenant_status", "tenant_id", "status"),
    )


class ConversationMessage(MysqlBase):
    """微信回调消息记录 — 用户向公众号发送的消息"""
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    openid = Column(String(128), nullable=False, comment="用户 OpenID")
    direction = Column(String(8), nullable=False, default="inbound", comment="inbound/outbound")

    # 消息类型
    message_type = Column(String(32), nullable=False, default="text", comment="text/image/voice/video/event")
    content = Column(Text, nullable=True, comment="消息内容（文本消息）")

    # 微信消息标识
    msg_id = Column(String(128), nullable=True, comment="微信 MsgId（文本消息有）")
    event_fingerprint = Column(String(64), nullable=True, comment="事件去重指纹（无 MsgId 时使用）")
    event_type = Column(String(64), nullable=True, comment="事件类型（event 消息）")
    event_key = Column(String(128), nullable=True, comment="事件KEY")

    # 时间
    create_time = Column(DateTime, nullable=True, comment="微信侧消息时间")
    received_at = Column(DateTime, nullable=False, server_default=func.now(), comment="系统接收时间")

    # 处理状态
    processing_status = Column(String(32), nullable=False, default="received",
                                comment="received/queued/processing/processed/ignored/failed/manual_review_required")
    processing_error = Column(Text, nullable=True)
    matched_lead_id = Column(Integer, nullable=True, comment="匹配到的线索ID")
    delivery_id = Column(Integer, nullable=True, comment="创建的发送任务ID")

    raw_xml = Column(JSON, nullable=True, comment="原始 XML 解析后的 JSON")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_conv_msg_account_openid", "account_id", "openid"),
        Index("ix_conv_msg_status", "processing_status"),
        Index("ix_conv_msg_matched_lead", "matched_lead_id"),
        UniqueConstraint("account_id", "msg_id", name="uq_conv_msg_id"),
        UniqueConstraint("account_id", "event_fingerprint", name="uq_conv_event_fp"),
    )


# ============================================================
# P2 用户互动状态
# ============================================================


class WeChatUserInteraction(MysqlBase):
    """用户互动状态 — 跟踪关注、会话窗口、最后互动时间"""
    __tablename__ = "wechat_user_interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    openid = Column(String(128), nullable=False, comment="用户 OpenID")

    # 关注状态
    is_following = Column(Boolean, default=False, nullable=False)
    follow_time = Column(DateTime, nullable=True)
    unfollow_time = Column(DateTime, nullable=True)

    # 最后互动时间
    last_inbound_at = Column(DateTime, nullable=True, comment="最后用户消息时间")
    last_text_at = Column(DateTime, nullable=True, comment="最后文本消息时间")
    last_event_at = Column(DateTime, nullable=True, comment="最后事件时间")

    # 会话窗口
    session_status = Column(String(16), nullable=False, default="none",
                            comment="none/active/expired")
    session_started_at = Column(DateTime, nullable=True)
    session_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "openid", name="uq_interaction_account_openid"),
        Index("ix_interaction_tenant", "tenant_id"),
    )


# ============================================================
# P1.1 联系资料包
# ============================================================


class ContactPackage(MysqlBase):
    """联系资料包 — 统一配置联系文案、微信号、电话和二维码"""
    __tablename__ = "contact_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    name = Column(String(128), nullable=False, comment="资料包名称")

    description = Column(String(255), nullable=True)

    # 联系方式
    contact_name = Column(String(64), nullable=True)
    wechat_id = Column(String(64), nullable=True)
    phone = Column(String(32), nullable=True)
    text_content = Column(Text, nullable=True, comment="发送给用户的欢迎语/联系文案")

    # 二维码
    qr_asset_id = Column(Integer, nullable=True, comment="本地素材 ID（FK → assets.id）")

    # 状态
    is_default = Column(Boolean, default=False, nullable=False)
    is_enabled = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    # 统计
    usage_count = Column(Integer, default=0, nullable=False)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "name", name="uq_pkg_tenant_account_name"),
        Index("ix_pkg_tenant_account", "tenant_id", "account_id"),
    )


# ============================================================
# P1.2 微信素材管理
# ============================================================


class WechatMediaAsset(MysqlBase):
    """微信素材标识 — 管理本地资产与微信 media_id 的映射"""
    __tablename__ = "wechat_media_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    asset_id = Column(Integer, nullable=False, comment="本地资产 ID（FK → assets.id）")

    media_type = Column(String(32), nullable=False, default="image")
    media_scope = Column(String(16), nullable=False, default="temporary", comment="temporary/permanent")
    media_id = Column(String(128), nullable=True, comment="微信素材 media_id")
    status = Column(String(16), nullable=False, default="missing",
                    comment="missing/pending/uploading/ready/expired/failed")
    is_mock = Column(Boolean, default=False, nullable=False, comment="是否为 mock 模式")

    uploaded_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
    response_snapshot = Column(JSON, nullable=True, comment="微信原始返回")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", "asset_id", "media_type",
                         name="uq_media_asset_per_account"),
        Index("ix_media_account_status", "account_id", "status"),
    )


# ============================================================
# P1.4 资料发送任务
# ============================================================


class ContactDelivery(MysqlBase):
    """资料发送任务 — 分步骤跟踪发送结果"""
    __tablename__ = "contact_deliveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=False)
    lead_id = Column(Integer, ForeignKey("comment_leads.id"), nullable=True)
    openid = Column(String(128), nullable=False)
    package_id = Column(Integer, nullable=True)

    # 幂等
    idempotency_key = Column(String(128), nullable=False)
    delivery_mode = Column(String(16), nullable=False, default="live", comment="live/mock")

    # 状态
    status = Column(String(32), nullable=False, default="pending",
                    comment="pending/checking_eligibility/preparing_media/sending_text/sending_qr/success/partial_failed/failed/ineligible/cancelled")

    # 快照
    package_snapshot = Column(JSON, nullable=True)
    eligibility_snapshot = Column(JSON, nullable=True)

    # 文本步骤
    text_status = Column(String(16), nullable=True, default=None,
                         comment="pending/processing/success/failed/skipped")
    text_attempts = Column(Integer, default=0, nullable=False)
    text_error_code = Column(String(64), nullable=True)
    text_error_message = Column(Text, nullable=True)
    text_sent_at = Column(DateTime, nullable=True)

    # 二维码步骤
    qr_status = Column(String(16), nullable=True, default=None,
                        comment="pending/processing/success/failed/skipped")
    qr_attempts = Column(Integer, default=0, nullable=False)
    qr_error_code = Column(String(64), nullable=True)
    qr_error_message = Column(Text, nullable=True)
    qr_sent_at = Column(DateTime, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_delivery_idempotency"),
        Index("ix_delivery_lead", "lead_id"),
        Index("ix_delivery_status", "status"),
    )


class ContactDeliveryAttempt(MysqlBase):
    """发送尝试记录 — 每次尝试的独立记录"""
    __tablename__ = "contact_delivery_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(Integer, ForeignKey("contact_deliveries.id", ondelete="CASCADE"), nullable=False)
    step = Column(String(16), nullable=False, comment="text/qr")
    attempt_no = Column(Integer, nullable=False)

    status = Column(String(16), nullable=False, default="processing",
                    comment="processing/success/failed")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    response_snapshot = Column(JSON, nullable=True)

    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_attempt_delivery", "delivery_id"),
    )
