"""创建投喂文章格式模板表，并为定时任务增加显式模板绑定字段。

迁移只新增空表、可空外键和自动绑定开关：所有既有任务的 ``format_profile_id`` 保持
为空且自动绑定关闭，因此包括已上线的绣蔓 ERP 仿写在内的历史任务不会被切换到新
格式管线。脚本可重复执行。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    """检查字段是否存在，确保 DDL 在重复部署时幂等。"""

    return connection.execute(text("""
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = :column_name
    """), {"table_name": table_name, "column_name": column_name}).scalar() is not None


def run_migration() -> None:
    """创建模板表并添加任务绑定字段，不修改任何既有任务配置。"""

    with mysql_engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS article_format_profiles (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                tenant_id INT NOT NULL,
                source_article_id INT NULL,
                name VARCHAR(255) NOT NULL,
                version INT NOT NULL DEFAULT 1,
                render_mode VARCHAR(32) NOT NULL,
                template_payload JSON NOT NULL,
                title_policy JSON NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX ix_article_format_profiles_tenant (tenant_id),
                INDEX ix_article_format_profiles_article (source_article_id),
                CONSTRAINT fk_article_format_profiles_tenant
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
                CONSTRAINT fk_article_format_profiles_source_article
                    FOREIGN KEY (source_article_id) REFERENCES feed_source_articles(id)
                    ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        if not _column_exists(connection, "scheduled_tasks", "format_profile_id"):
            connection.execute(text("""
                ALTER TABLE scheduled_tasks
                ADD COLUMN format_profile_id INT NULL
                COMMENT '任务显式绑定的投喂文章格式模板版本',
                ADD INDEX ix_scheduled_tasks_format_profile (format_profile_id),
                ADD CONSTRAINT fk_scheduled_tasks_format_profile
                    FOREIGN KEY (format_profile_id) REFERENCES article_format_profiles(id)
                    ON DELETE SET NULL
            """))
            logger.info("已添加 scheduled_tasks.format_profile_id，历史任务保持为空")
        else:
            logger.info("scheduled_tasks.format_profile_id 已存在")
        if not _column_exists(
            connection,
            "scheduled_tasks",
            "format_profile_auto_bind_enabled",
        ):
            connection.execute(text("""
                ALTER TABLE scheduled_tasks
                ADD COLUMN format_profile_auto_bind_enabled BOOLEAN NOT NULL DEFAULT FALSE
                COMMENT '是否允许投喂源自动绑定格式模板，新任务开启，历史任务关闭'
            """))
            logger.info("已添加自动格式绑定开关，历史任务默认关闭")
        else:
            logger.info("scheduled_tasks.format_profile_auto_bind_enabled 已存在")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
