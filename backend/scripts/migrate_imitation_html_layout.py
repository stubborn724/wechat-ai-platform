"""为仿写任务添加 HTML 版式模式字段。

脚本可重复执行：部署时先运行本脚本，再重启 API 与 Celery worker。旧任务统一写入
``content``，因此升级不会改变已经存在的仿写任务行为。
"""

import logging
import sys
from pathlib import Path

# 迁移脚本通常由绝对路径或 ``python scripts/...`` 直接启动，此时 Python 只会把
# scripts 目录加入模块搜索路径。显式定位 backend 根目录，保证部署命令不依赖
# 当前工作目录和额外的 PYTHONPATH 配置。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database import mysql_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration() -> None:
    """仅在字段不存在时执行 ALTER TABLE，保证重复部署安全。"""

    with mysql_engine.begin() as connection:
        column_name = connection.execute(text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'imitation_tasks'
              AND COLUMN_NAME = 'imitation_mode'
        """)).scalar()
        if column_name is not None:
            logger.info("imitation_tasks.imitation_mode 已存在")
            return

        connection.execute(text("""
            ALTER TABLE imitation_tasks
            ADD COLUMN imitation_mode VARCHAR(32) NOT NULL DEFAULT 'content'
            COMMENT '仿写模式: content=内容结构, html_layout=保留HTML版式'
        """))
        logger.info("已添加 imitation_tasks.imitation_mode")


if __name__ == "__main__":
    run_migration()
