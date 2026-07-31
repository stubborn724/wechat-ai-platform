"""扩大文章 HTML 正文列，兼容公众号版式仿写文章。

HTML 仿写会保留原文 DOM 的节点属性、内联样式和图片节点。图片数量增加后，
完整正文很容易超过 MySQL ``TEXT`` 的 64 KiB 上限，因此 ``content`` 与
``full_content`` 都统一升级为 ``MEDIUMTEXT``。迁移只扩大存储类型，不截断已有
数据，并且重复执行时不会重复修改列。
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text

# 直接运行脚本时，Python 默认只把 scripts 目录加入导入路径；补齐 backend 根目录，
# 使迁移脚本与其他部署脚本一样可以从任意工作目录执行。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import mysql_engine


logger = logging.getLogger(__name__)

# 使用固定列名集合构造 DDL，避免把外部输入拼接到 SQL 标识符中。
ARTICLE_HTML_COLUMNS = ("content", "full_content")
SUPPORTED_LARGE_TYPES = {"mediumtext", "longtext"}


def run_migration() -> None:
    """将文章 HTML 字段升级为可容纳大版式正文的类型。

    迁移先读取实际数据库类型，再只处理尚未达到 ``MEDIUMTEXT`` 容量的列；这样
    历史环境、已执行环境和后续采用 ``LONGTEXT`` 的环境都可以安全重复运行。
    """

    with mysql_engine.begin() as connection:
        rows = connection.execute(text("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'articles'
              AND COLUMN_NAME IN ('content', 'full_content')
        """)).mappings().all()
        column_types = {
            str(row["COLUMN_NAME"]).lower(): str(row["DATA_TYPE"]).lower()
            for row in rows
        }

        missing_columns = [
            column for column in ARTICLE_HTML_COLUMNS if column not in column_types
        ]
        if missing_columns:
            raise RuntimeError(
                "articles 表缺少文章 HTML 字段: " + ", ".join(missing_columns)
            )

        for column in ARTICLE_HTML_COLUMNS:
            current_type = column_types[column]
            if current_type in SUPPORTED_LARGE_TYPES:
                logger.info("articles.%s 已是 %s，跳过", column, current_type.upper())
                continue

            connection.execute(
                text(f"ALTER TABLE articles MODIFY COLUMN `{column}` MEDIUMTEXT NULL")
            )
            logger.info("已将 articles.%s 从 %s 升级为 MEDIUMTEXT", column, current_type.upper())

        logger.info("文章 HTML 存储迁移完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
