"""为定时任务添加可为空的任务级水印配置快照。

空值保持历史任务的全局水印行为；只有明确写入 JSON 的任务才固定自己的水印，
因此该迁移不会改写文章、运行记录或任何已有任务配置。脚本可重复执行。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

# 直接执行 scripts 下的文件时，Python 默认不会把 backend 根目录加入 sys.path。
# 根据脚本位置补齐模块路径，保持迁移命令与其他部署脚本一致。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)


def run_migration() -> None:
    """仅添加 nullable JSON 列，重复执行不会修改任何任务数据。"""

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
              AND COLUMN_NAME = 'watermark_config'
        """)).scalar()
        if column_exists is not None:
            logger.info("scheduled_tasks.watermark_config 已存在")
            return

        connection.execute(text("""
            ALTER TABLE scheduled_tasks
            ADD COLUMN watermark_config JSON NULL
            COMMENT '任务级水印配置快照'
        """))
        logger.info("已添加 scheduled_tasks.watermark_config")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
