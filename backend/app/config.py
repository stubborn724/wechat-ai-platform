"""应用配置"""

import os
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"
# 后端专属运行配置与根目录通用配置分离。后者保留既有部署习惯，前者可存放
# ERP 等仅服务端需要的接入参数；后加载的后端配置会覆盖根目录同名字段。
BACKEND_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # Server
    server_port: int = 8002
    server_host: str = "0.0.0.0"
    environment: str = "development"

    # MySQL - 业务数据库
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "root123"
    mysql_database: str = "wechat_platform"

    # PostgreSQL - 向量数据库
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "wechat"
    pg_password: str = "wechat123"
    pg_database: str = "wechat_vector"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # JWT
    jwt_secret_key: str = "change-this-to-a-random-secret-key"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Credential Encryption
    credential_key: str = "change-this-to-a-32-char-key!!"

    # 微信发送模式: live 真实发送 / mock 模拟发送
    wechat_send_mode: str = "mock"

    # 微信官方 API 调用通道。
    # direct：本机后端直连微信官方接口，需要用户配置 IP 白名单；
    # relay：本机后端只调用固定 IP 中转站，由中转站访问微信官方接口。
    wechat_api_channel: str = "direct"
    wechat_relay_base_url: str = ""
    wechat_relay_app_id: str = "relay_client"
    wechat_relay_secret: str = ""
    # 中转站目前没有最终发布状态查询接口。超过该时限仍未确认的 relay 发布会明确
    # 收敛为可人工核验的失败，防止任务无限停留在 publishing。
    wechat_relay_publish_status_timeout_seconds: int = 900

    # 内容 Worker 每个长耗时阶段都会写入心跳。图片生成可能在正文完成后串行执行多次，
    # 本地联调窗口必须覆盖完整素材链路；超过 45 分钟仍没有新心跳才判定为 Worker 重启、
    # 旧版本进程或消息丢失后的遗留任务，避免 Gateway 和桌面端永久显示“正在生成”。
    tageai_generation_heartbeat_timeout_seconds: int = 2700

    # ERP 产品素材源。配置为 JSON 数组，密钥仅允许保存在后端 .env 中，
    # 浏览器和文章任务参数都不能携带 ERP 应用凭证。
    # 结构示例见 .env.example：key、name、client_id、client_secret、product_api_path。
    erp_product_api_base_url: str = ""
    erp_product_tenant_id: str = "1"
    erp_product_sources_json: str = "[]"

    # 腾讯 COS 仅承担私有图片的短时公网中转，不替代 MinIO 长期素材存储。
    # 凭证只允许由服务端环境变量注入；endpoint 是可选的非签名可读基址，
    # SDK 仍依据 region 和 bucket 生成请求地址与带时效的 HTTPS 签名地址。
    cos_enabled: bool = False
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_bucket: str = ""
    cos_endpoint: str = ""
    cos_signed_url_expire_seconds: int = 3600

    # DashScope AI
    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-plus"

    # 文生文使用独立的 OpenAI 兼容主站，百炼作为第二层兜底。密钥仅由服务端
    # 环境变量注入；所有文章 Agent 通过统一路由调用，避免模型配置散落。
    text_generation_provider_chain: str = "kuai,dashscope"
    text_generation_base_url: str = ""
    text_generation_api_key: str = ""
    text_generation_model: str = "gpt-5-mini"
    # 正文提供商包含主备切换，本地验收允许单篇正文最多等待 15 分钟。
    text_generation_timeout_seconds: int = 900

    # AI 图片生成使用独立于正文大模型的提供商配置。业务层只识别统一图片生成
    # 服务，主备提供商及模型由这里选择，避免每个 Agent 各自硬编码模型地址。
    # 默认值与示例配置保持一致：优先使用 OpenAI 兼容主站，避免新环境在未显式
    # 配置 provider chain 时意外回退到历史万相路径。
    image_generation_provider: str = "kuai_openai_compatible"
    image_generation_provider_chain: str = ""
    image_generation_base_url: str = ""
    image_generation_api_key: str = ""
    image_generation_model: str = "gpt-image-2"
    image_generation_edit_model: str = "gpt-image-2"
    image_generation_secondary_base_url: str = ""
    image_generation_secondary_api_key: str = ""
    image_generation_secondary_model: str = "gpt-image-2"
    image_generation_secondary_edit_model: str = "gpt-image-2"
    # 火山方舟 Seedream 是万相的图生图替代兜底。它可以接收本地图片字节，
    # 因而不要求 ERP 原图先暴露为公网 URL。
    image_generation_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    image_generation_ark_api_key: str = ""
    image_generation_ark_model: str = "doubao-seedream-4-0-250828"
    # 历史全局超时仅作为兼容回退。生产链路必须优先采用下方按 Provider 划分的
    # 超时，避免单个异常中转站把整篇定时文章阻塞 30 分钟。
    image_generation_timeout_seconds: int = 1800
    image_generation_primary_timeout_seconds: int = 120
    image_generation_secondary_timeout_seconds: int = 150
    image_generation_ark_timeout_seconds: int = 180
    # 同一 Provider 连续临时故障时，后续图片直接走备用链路，避免五张图重复等待。
    image_provider_circuit_failure_threshold: int = 3
    image_provider_circuit_cooldown_seconds: int = 600
    # 定时文章按任务维度互斥，但不同品牌可以在有限槽位中并行，消除全局串行排队。
    scheduled_task_max_active_runs: int = 2
    # 同一篇草稿会向多个公众号发起独立 HTTP 请求；限制为两个并行账号，既缩短
    # 五账号场景的尾部时间，也避免中转站或微信接口因突发并发产生限流。
    scheduled_draft_delivery_max_workers: int = 2
    # 旧万相适配器仅用于兼容历史部署；新部署默认以方舟 Seedream 作为最终兜底。
    image_generation_fallback_provider: str = "volcengine_ark"
    image_generation_max_response_bytes: int = 20 * 1024 * 1024

    # 文章图片的动态署名。产品名称由本次 ERP 选品或文章标题提供，联系方式集中
    # 配置在服务端，避免写入投喂源 Prompt 后被来源文章或模型输出覆盖。
    article_image_brand_contact: str = "绣蔓家具TEL:18682130473"

    # MinIO
    minio_endpoint: str = "localhost:9002"
    minio_public_endpoint: str = "http://localhost:9002"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "wechat-assets"
    minio_use_ssl: bool = False
    # 发布 Worker 可能运行在 Docker 内，而历史文章地址可能由宿主机生成。
    # 这些地址都指向同一个 MinIO 桶；中转服务只把它们解析成对象键，不会把
    # 地址直接交给外部服务访问。默认保留本机常用端口，生产环境可通过环境变量
    # 增加旧域名或迁移前的访问入口，避免历史文章因部署拓扑变化无法发布。
    minio_url_aliases: str = "http://localhost:9002,http://127.0.0.1:9002"

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/1"

    # TaGeAI Integration API 配置
    # tageai_integration_clients 可以是单个 dict 或 list[dict]
    # 每个 client 包含: client_id, signing_secret, tenant_binding_id, tenant_id
    tageai_integration_clients: dict | list[dict] | str = ""

    model_config = SettingsConfigDict(
        env_file=(str(ENV_FILE), str(BACKEND_ENV_FILE)),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def mysql_url(self) -> str:
        return f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"

    @property
    def pg_url(self) -> str:
        return f"postgresql+psycopg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
