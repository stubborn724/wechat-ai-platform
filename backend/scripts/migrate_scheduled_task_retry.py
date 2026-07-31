"""为定时任务运行记录增加有限重试与 Worker 恢复字段。

执行记录不能只依赖 Celery 内存状态。Worker 重启后，数据库需要知道尝试次数、
下一次重试时间和当前消息 ID，Beat 才能接管遗留的 running 记录并避免无限重试。
迁移逐列检查，支持历史环境重复执行。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

# 直接执行脚本时补齐 backend 根目录，保持与其他项目迁移脚本一致的运行方式。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)

RETRY_COLUMNS = {
    "attempt_count": (
        "ALTER TABLE scheduled_task_runs "
        "ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 "
        "COMMENT '当前执行尝试次数，包含初次执行'"
    ),
    "next_retry_at": (
        "ALTER TABLE scheduled_task_runs "
        "ADD COLUMN next_retry_at DATETIME NULL COMMENT '下一次允许重试的时间'"
    ),
    "celery_task_id": (
        "ALTER TABLE scheduled_task_runs "
        "ADD COLUMN celery_task_id VARCHAR(255) NULL COMMENT '当前 Celery 消息 ID'"
    ),
    "delivery_results": (
        "ALTER TABLE scheduled_task_runs "
        "ADD COLUMN delivery_results JSON NULL COMMENT '按文章和公众号记录外部交付结果'"
    ),
}


def _has_column(connection, column_name: str) -> bool:
    """查询当前数据库是否已经存在指定字段。"""

    return connection.execute(
        text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_task_runs'
              AND COLUMN_NAME = :column_name
        """),
        {"column_name": column_name},
    ).scalar() is not None


def _has_index(connection, index_name: str) -> bool:
    """查询当前数据库是否已经存在恢复查询索引。"""

    return connection.execute(
        text("""
            SELECT INDEX_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_task_runs'
              AND INDEX_NAME = :index_name
            LIMIT 1
        """),
        {"index_name": index_name},
    ).scalar() is not None


def run_migration() -> None:
    """幂等添加定时任务重试字段和恢复索引。"""

    with mysql_engine.begin() as connection:
        table_exists = connection.execute(text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_task_runs'
        """)).scalar()
        if table_exists is None:
            raise RuntimeError("scheduled_task_runs 表不存在，请先执行 ERP 定时任务迁移")

        for column_name, statement in RETRY_COLUMNS.items():
            if _has_column(connection, column_name):
                logger.info("scheduled_task_runs.%s 已存在，跳过", column_name)
                continue
            connection.execute(text(statement))
            logger.info("已添加 scheduled_task_runs.%s", column_name)

        if _has_index(connection, "ix_scheduled_task_runs_recovery"):
            logger.info("恢复索引已存在，跳过")
        else:
            connection.execute(text("""
                CREATE INDEX ix_scheduled_task_runs_recovery
                ON scheduled_task_runs (status, next_retry_at)
            """))
            logger.info("已添加 scheduled_task_runs 恢复索引")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
