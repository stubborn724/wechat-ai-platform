"""应用配置"""

import os
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"


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

    # DashScope AI
    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-plus"

    # Pexels
    pexels_api_key: str = ""

    # MinIO
    minio_endpoint: str = "localhost:9002"
    minio_public_endpoint: str = "http://localhost:9002"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "wechat-assets"
    minio_use_ssl: bool = False

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/1"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
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
