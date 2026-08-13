"""为定时任务添加来源模板轮换配置与时段快照字段。

迁移只增加可空配置列、默认版本列和查询索引，不会写入或调整任何现有定时任务。
因此历史绣蔓任务与已经排队的发布记录继续使用原有单模板执行路径。脚本可重复
执行，适合开发机、测试环境和生产环境部署时安全调用。
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


COLUMNS = {
    ("scheduled_tasks", "template_rotation_config"): (
        "ALTER TABLE scheduled_tasks ADD COLUMN template_rotation_config JSON NULL "
        "COMMENT '来源模板轮换配置；为空时禁用轮换'"
    ),
    ("scheduled_tasks", "template_rotation_version"): (
        "ALTER TABLE scheduled_tasks ADD COLUMN template_rotation_version INT NOT NULL "
        "DEFAULT 0 COMMENT '来源模板轮换配置版本'"
    ),
    ("scheduled_task_runs", "format_profile_id"): (
        "ALTER TABLE scheduled_task_runs ADD COLUMN format_profile_id INT NULL "
        "COMMENT '本次时段冻结的来源格式模板'"
    ),
    ("scheduled_task_runs", "template_rotation_version"): (
        "ALTER TABLE scheduled_task_runs ADD COLUMN template_rotation_version INT NULL "
        "COMMENT '创建本次时段时使用的模板轮换配置版本'"
    ),
}


def _has_column(connection, table_name: str, column_name: str) -> bool:
    """查询字段是否存在，保证不同环境可以安全重复执行迁移。"""

    return connection.execute(
        text(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar() is not None


def _has_index(connection, index_name: str) -> bool:
    """查询索引是否存在，避免重复创建导致部署失败。"""

    return connection.execute(
        text(
            """
            SELECT INDEX_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_task_runs'
              AND INDEX_NAME = :index_name
            LIMIT 1
            """
        ),
        {"index_name": index_name},
    ).scalar() is not None


def run_migration() -> None:
    """幂等增加轮换字段和读取历史时段所需的索引。"""

    with mysql_engine.begin() as connection:
        for (table_name, column_name), statement in COLUMNS.items():
            table_exists = connection.execute(
                text(
                    """
                    SELECT TABLE_NAME
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
                    """
                ),
                {"table_name": table_name},
            ).scalar()
            if table_exists is None:
                raise RuntimeError(f"{table_name} 表不存在，无法添加模板轮换字段")
            if _has_column(connection, table_name, column_name):
                logger.info("%s.%s 已存在，跳过", table_name, column_name)
                continue
            connection.execute(text(statement))
            logger.info("已添加 %s.%s", table_name, column_name)

        index_name = "ix_scheduled_task_runs_rotation"
        if _has_index(connection, index_name):
            logger.info("%s 已存在，跳过", index_name)
        else:
            connection.execute(
                text(
                    """
                    CREATE INDEX ix_scheduled_task_runs_rotation
                    ON scheduled_task_runs
                    (task_id, template_rotation_version, scheduled_date, scheduled_time)
                    """
                )
            )
            logger.info("已添加 %s", index_name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
