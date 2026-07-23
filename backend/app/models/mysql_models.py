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
    msg_data_id = Column(String(128), nullable=True, comment="微信发布后返回的 msg_data_id，用于同步评论")
    publish_id = Column(String(128), nullable=True, comment="微信发布任务 ID（publish_id），用于查询发布状态")
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

    # 知识库
    knowledge_base_ids = Column(JSON, nullable=True)

    # 日程: -1=每天, 0-6=周几
    day_of_week = Column(Integer, default=-1)
    publish_times = Column(JSON, nullable=False)

    # 内容配置
    article_slots = Column(JSON, nullable=True)
    articles_per_day = Column(Integer, default=1)
    public_count = Column(Integer, default=1)
    private_count = Column(Integer, default=0)

    # 发布配置
    approval_mode = Column(String(32), default="auto")
    account_id = Column(Integer, ForeignKey("wechat_accounts.id"), nullable=True)
    account_ids = Column(JSON, nullable=True, comment="多选公众号 ID 列表，优先级高于 account_id")
    publish_mode = Column(String(32), default="draft", comment="draft=存草稿箱, direct=直接发布")
    image_source = Column(String(64), default="pexels", comment="图片来源: pexels/local/DASHSCOPE")
    footer_template = Column(Text, nullable=True)

    # 统计
    total_generated = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


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
