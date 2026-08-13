"""为公众号发布链路增加公域/私域字段。

任务级字段决定新时段默认走哪个交付域，运行记录和文章字段保存实际快照，
这样任务编辑或重试不会把已经排队的公域发布误切换为私域群发。迁移只添加
字段并使用公域默认值，不修改历史任务、文章和运行结果。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

# 直接执行脚本时补齐 backend 根目录，保持与项目其他迁移脚本一致。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)


COLUMNS = {
    ("scheduled_tasks", "publish_domain"): (
        "ALTER TABLE scheduled_tasks ADD COLUMN publish_domain VARCHAR(32) "
        "NOT NULL DEFAULT 'public' "
        "COMMENT 'direct 发布域：public=公域，private=私域'"
    ),
    ("scheduled_task_runs", "publish_domain"): (
        "ALTER TABLE scheduled_task_runs ADD COLUMN publish_domain VARCHAR(32) "
        "NOT NULL DEFAULT 'public' "
        "COMMENT '本次时段锁定的发布域：public=公域，private=私域'"
    ),
    ("articles", "publish_domain"): (
        "ALTER TABLE articles ADD COLUMN publish_domain VARCHAR(32) NULL "
        "COMMENT '实际交付域：public=公域，private=私域；历史文章为空'"
    ),
}


def _has_column(connection, table_name: str, column_name: str) -> bool:
    """查询字段是否存在，使迁移可在开发机和部署机重复执行。"""

    return connection.execute(
        text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
        """),
        {"table_name": table_name, "column_name": column_name},
    ).scalar() is not None


def run_migration() -> None:
    """为三张业务表幂等添加发布域字段。"""

    with mysql_engine.begin() as connection:
        for (table_name, column_name), statement in COLUMNS.items():
            table_exists = connection.execute(
                text("""
                    SELECT TABLE_NAME
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                """),
                {"table_name": table_name},
            ).scalar()
            if table_exists is None:
                raise RuntimeError(f"{table_name} 表不存在，无法添加 {column_name}")
            if _has_column(connection, table_name, column_name):
                logger.info("%s.%s 已存在，跳过", table_name, column_name)
                continue
            connection.execute(text(statement))
            logger.info("已添加 %s.%s", table_name, column_name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
