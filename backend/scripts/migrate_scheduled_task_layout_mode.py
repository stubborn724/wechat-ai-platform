"""为统一定时任务添加显式文章版式字段。

历史任务统一写入 ``standard``，从而保证新增海报能力不会改变任何已有任务的
输出。脚本只执行幂等 DDL，不读取或修改文章、运行记录和发布结果。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

# 直接执行 scripts 下的文件时，Python 默认不会把 backend 根目录加入 sys.path。
# 根据脚本位置补齐模块路径，保持迁移命令与其他部署脚本的调用方式一致。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)


def run_migration() -> None:
    """仅在字段不存在时添加非空默认列，重复执行不会改动既有任务数据。"""

    with mysql_engine.begin() as connection:
        table_exists = connection.execute(text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_tasks'
        """)).scalar()
        if table_exists is None:
            raise RuntimeError("scheduled_tasks 表不存在，请先执行定时任务基础迁移")

        column_exists = connection.execute(text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_tasks'
              AND COLUMN_NAME = 'layout_mode'
        """)).scalar()
        if column_exists is not None:
            logger.info("scheduled_tasks.layout_mode 已存在")
            return

        connection.execute(text("""
            ALTER TABLE scheduled_tasks
            ADD COLUMN layout_mode VARCHAR(32) NOT NULL DEFAULT 'standard'
            COMMENT '文章版式: standard=历史默认, seamless_poster=无缝海报'
        """))
        logger.info("已添加 scheduled_tasks.layout_mode，历史任务默认 standard")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
