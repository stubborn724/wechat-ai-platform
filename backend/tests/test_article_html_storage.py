"""文章 HTML 正文存储容量的回归测试。

公众号原始版式通常包含大量内联样式、属性和图片地址，不能继续使用 MySQL
``TEXT`` 的 64 KiB 上限。本测试同时约束 ORM 类型和迁移覆盖范围，避免只改一层
导致新环境与历史数据库结构再次分叉。
"""

import pytest
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.models.mysql_models import Article
from scripts.migrate_article_html_content import ARTICLE_HTML_COLUMNS


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只验证 ORM 类型和迁移契约，不连接或清理业务数据库。"""

    yield


def test_article_html_fields_use_mediumtext() -> None:
    """验证文章正文和最终 HTML 字段都能容纳公众号版式正文。"""

    # 两列都可能保存完整 HTML；只扩大其中一列会让保存过程在下一列再次失败。
    assert isinstance(Article.__table__.c.content.type, MEDIUMTEXT)
    assert isinstance(Article.__table__.c.full_content.type, MEDIUMTEXT)


def test_article_html_migration_covers_both_html_fields() -> None:
    """验证数据库迁移不会遗漏任一篇文章 HTML 字段。"""

    assert set(ARTICLE_HTML_COLUMNS) == {"content", "full_content"}
