"""第二阶段数据库迁移：纯 SQL 方式，避免 concurrent DDL 冲突

用 SQL 文件执行，不依赖 SQLAlchemy create_all。
用法: python -m scripts.migrate_v2
"""

import logging

from sqlalchemy import text

from app.database import mysql_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 新增表
CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS article_metrics (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        article_id INTEGER NOT NULL,
        wechat_account_id INTEGER NULL,
        metric_date DATE NOT NULL,
        fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_count INTEGER DEFAULT 0,
        like_count INTEGER DEFAULT 0,
        share_count INTEGER DEFAULT 0,
        comment_count INTEGER DEFAULT 0,
        add_to_fav_count INTEGER DEFAULT 0,
        exposure_count INTEGER NULL,
        read_user_count INTEGER NULL,
        raw_payload JSON NULL,
        sync_status VARCHAR(32) DEFAULT 'pending',
        error_code VARCHAR(64) NULL,
        error_message TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_article_metrics_date (article_id, metric_date),
        INDEX ix_article_metrics_date (metric_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS article_quality_evaluations (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        article_id INTEGER NOT NULL,
        content_score INTEGER NULL,
        readability_score INTEGER NULL,
        structure_score INTEGER NULL,
        value_score INTEGER NULL,
        title_score INTEGER NULL,
        title_consistency_score INTEGER NULL,
        credibility_score INTEGER NULL,
        overall_score INTEGER NULL,
        issues JSON NULL,
        suggestions JSON NULL,
        rewrite_recommended TINYINT(1) DEFAULT 0,
        rewrite_scope VARCHAR(64) NULL,
        factual_risk VARCHAR(32) NULL,
        brand_risk VARCHAR(32) NULL,
        confidence FLOAT NULL,
        model_name VARCHAR(128) NOT NULL,
        model_version VARCHAR(64) NULL,
        prompt_version VARCHAR(64) NULL,
        input_content_hash VARCHAR(64) NULL,
        raw_response JSON NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        error_message TEXT NULL,
        evaluated_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX ix_quality_eval_article (article_id),
        INDEX ix_quality_eval_hash (input_content_hash),
        INDEX ix_quality_eval_article_status (article_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS article_optimizations (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        tenant_id INTEGER NOT NULL,
        source_article_id INTEGER NOT NULL,
        optimized_article_id INTEGER NULL,
        trigger_type VARCHAR(32) NOT NULL DEFAULT 'auto',
        trigger_evaluation_id INTEGER NULL,
        optimization_type VARCHAR(64) NOT NULL,
        optimization_generation INTEGER DEFAULT 1,
        optimization_instruction TEXT NULL,
        model_name VARCHAR(128) NULL,
        model_version VARCHAR(64) NULL,
        prompt_version VARCHAR(64) NULL,
        change_summary TEXT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'created',
        reviewer_id INTEGER NULL,
        reviewed_at DATETIME NULL,
        review_comment TEXT NULL,
        published_at DATETIME NULL,
        comparison_result VARCHAR(32) NULL,
        comparison_summary TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX ix_article_optimizations_tenant (tenant_id),
        INDEX ix_article_optimizations_source (source_article_id),
        INDEX ix_article_optimizations_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS task_execution_logs (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        task_name VARCHAR(128) NOT NULL,
        task_id VARCHAR(128) NULL,
        tenant_id INTEGER NULL,
        account_id INTEGER NULL,
        article_id INTEGER NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'running',
        started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at DATETIME NULL,
        retry_count INTEGER DEFAULT 0,
        error_code VARCHAR(64) NULL,
        error_message TEXT NULL,
        extra_data JSON NULL COMMENT '任务附加数据',
        INDEX ix_task_log_name (task_name),
        INDEX ix_task_log_name_status (task_name, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# 新增字段: (表名, 字段定义)
ALTER_COLUMNS = [
    # ScheduledTask
    ("scheduled_tasks", "ADD COLUMN pool_id INTEGER NULL"),
    ("scheduled_tasks", "ADD COLUMN strategy VARCHAR(32) NULL"),
    # Article
    ("articles", "ADD COLUMN wechat_account_id INTEGER NULL"),
    ("articles", "ADD COLUMN wechat_publish_time DATETIME NULL"),
    ("articles", "ADD COLUMN latest_read_count INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN latest_like_count INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN latest_share_count INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN latest_comment_count INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN latest_fav_count INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN metrics_updated_at DATETIME NULL"),
    ("articles", "ADD COLUMN latest_quality_score INTEGER NULL"),
    ("articles", "ADD COLUMN quality_evaluated_at DATETIME NULL"),
    ("articles", "ADD COLUMN source_article_id INTEGER NULL"),
    ("articles", "ADD COLUMN optimization_generation INTEGER DEFAULT 0"),
    ("articles", "ADD COLUMN optimization_status VARCHAR(32) NULL"),
    ("articles", "ADD COLUMN manual_optimization_disabled TINYINT(1) DEFAULT 0"),
    # ImitationTask
    ("imitation_tasks", "ADD COLUMN title VARCHAR(255) NULL COMMENT '用户指定的标题'"),
]

# 新增索引
ALTER_INDEXES = [
    ("articles", "ADD INDEX ix_articles_wechat_account (wechat_account_id)"),
    ("articles", "ADD INDEX ix_articles_source (source_article_id)"),
]


def run_migration():
    conn = mysql_engine.connect()
    try:
        # 创建新表
        logger.info("创建新表...")
        for sql in CREATE_TABLES:
            try:
                conn.execute(text(sql))
                # 提取表名用于日志
                tbl = sql.split("CREATE TABLE IF NOT EXISTS")[1].strip().split(" ")[0].strip("`")
                logger.info("  + 表 %s 已就绪", tbl)
            except Exception as e:
                logger.warning("  ! 建表失败: %s", e)

        # 加字段
        logger.info("迁移现有表字段...")
        for tbl, col_def in ALTER_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} {col_def}"))
                logger.info("  + %s: %s", tbl, col_def[:60])
            except Exception as e:
                err = str(e)
                if "Duplicate column" in err or "already exists" in err:
                    logger.info("  ~ %s: %s (已存在)", tbl, col_def[:40])
                elif "doesn't exist" in err:
                    logger.warning("  ! 表 %s 不存在，跳过后续字段", tbl)
                    break
                else:
                    logger.warning("  ! %s: %s", tbl, err)

        # 加索引
        logger.info("创建索引...")
        for tbl, idx_def in ALTER_INDEXES:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} {idx_def}"))
                logger.info("  + %s: %s", tbl, idx_def[:60])
            except Exception as e:
                err = str(e)
                if "Duplicate key name" in err or "already exists" in err:
                    logger.info("  ~ %s: 索引已存在", tbl)
                elif "doesn't exist" in err:
                    break
                else:
                    logger.warning("  ! %s: %s", tbl, err)

        logger.info("迁移完成！请重启应用")

    except Exception as exc:
        logger.error("迁移失败: %s", exc)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
