"""为 ERP 分类配图定时任务添加持久化表与配置字段。

可重复执行：已存在的字段、索引和表会被安全跳过。生产部署应先运行本脚本，再重启
API、Celery worker 和 scheduler，避免旧表结构加载新 ORM 模型时失败。
"""

import logging

from sqlalchemy import text

from app.database import mysql_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS scheduled_task_runs (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        task_id INTEGER NOT NULL,
        scheduled_date DATE NOT NULL,
        scheduled_time VARCHAR(5) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'queued',
        article_id INTEGER NULL,
        error_message TEXT NULL,
        started_at DATETIME NULL,
        finished_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_scheduled_task_run_slot (task_id, scheduled_date, scheduled_time),
        INDEX ix_scheduled_task_runs_task_date (task_id, scheduled_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_task_erp_image_usages (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        task_id INTEGER NOT NULL,
        run_id INTEGER NOT NULL,
        asset_id INTEGER NULL,
        erp_image_url VARCHAR(2048) NOT NULL,
        product_name VARCHAR(255) NOT NULL,
        used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX ix_scheduled_task_erp_image_window (task_id, used_at),
        INDEX ix_scheduled_task_erp_image_run (run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def run_migration() -> None:
    """执行当前功能所需的最小、可重复数据库迁移。"""
    with mysql_engine.begin() as connection:
        column = connection.execute(text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_tasks'
              AND COLUMN_NAME = 'erp_image_config'
        """)).scalar()
        if column is None:
            connection.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN erp_image_config JSON NULL"))
            logger.info("已添加 scheduled_tasks.erp_image_config")
        else:
            logger.info("scheduled_tasks.erp_image_config 已存在")

        for statement in CREATE_TABLES:
            connection.execute(text(statement))

        selection_scope_exists = connection.execute(text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_task_erp_image_usages'
              AND COLUMN_NAME = 'selection_scope'
        """)).scalar()
        if selection_scope_exists is None:
            connection.execute(text("""
                ALTER TABLE scheduled_task_erp_image_usages
                ADD COLUMN selection_scope VARCHAR(128) NULL
                COMMENT 'ERP 防重范围；为空时兼容旧任务按 task_id 防重'
            """))
            connection.execute(text("""
                CREATE INDEX ix_scheduled_erp_image_scope_window
                ON scheduled_task_erp_image_usages (selection_scope, used_at)
            """))
            logger.info("已添加 scheduled_task_erp_image_usages.selection_scope")
        else:
            logger.info("scheduled_task_erp_image_usages.selection_scope 已存在")
        logger.info("ERP 定时任务迁移完成")


if __name__ == "__main__":
    run_migration()
