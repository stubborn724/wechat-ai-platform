"""为统一定时任务添加 HTML 仿写图片数量配置。

脚本可从任意工作目录直接运行，并且重复执行安全。历史任务统一使用默认五张，
只有新任务显式配置时才会提高 HTML 仿写图片数量。
"""

import logging
import sys
from pathlib import Path

# 直接执行 scripts 下的文件时，Python 默认不会把 backend 根目录加入 sys.path。
# 根据脚本位置补齐模块路径，避免部署命令依赖当前目录或外部 PYTHONPATH。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database import mysql_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration() -> None:
    """仅在字段缺失时添加非空默认列，保证重复部署不会重复执行 DDL。"""

    with mysql_engine.begin() as connection:
        column_name = connection.execute(text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'scheduled_tasks'
              AND COLUMN_NAME = 'html_image_count'
        """)).scalar()
        if column_name is not None:
            logger.info("scheduled_tasks.html_image_count 已存在")
            return

        connection.execute(text("""
            ALTER TABLE scheduled_tasks
            ADD COLUMN html_image_count INT NOT NULL DEFAULT 5
            COMMENT 'HTML仿写每篇生成图片数量，默认5，范围1-30'
        """))
        logger.info("已添加 scheduled_tasks.html_image_count")


if __name__ == "__main__":
    run_migration()
