-- ============================================
-- 第二阶段数据库迁移 SQL
-- 用法: 先关闭后端，然后执行:
--   mysql -u root -p wechat_platform < scripts/migrate_v2.sql
-- ============================================

-- 1. ScheduledTask 新增字段
ALTER TABLE scheduled_tasks ADD COLUMN pool_id INTEGER NULL;
ALTER TABLE scheduled_tasks ADD COLUMN strategy VARCHAR(32) NULL;

-- 2. Article 新增字段
ALTER TABLE articles ADD COLUMN wechat_account_id INTEGER NULL;
ALTER TABLE articles ADD COLUMN wechat_publish_time DATETIME NULL;
ALTER TABLE articles ADD COLUMN latest_read_count INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN latest_like_count INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN latest_share_count INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN latest_comment_count INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN latest_fav_count INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN metrics_updated_at DATETIME NULL;
ALTER TABLE articles ADD COLUMN latest_quality_score INTEGER NULL;
ALTER TABLE articles ADD COLUMN quality_evaluated_at DATETIME NULL;
ALTER TABLE articles ADD COLUMN source_article_id INTEGER NULL;
ALTER TABLE articles ADD COLUMN optimization_generation INTEGER DEFAULT 0;
ALTER TABLE articles ADD COLUMN optimization_status VARCHAR(32) NULL;
ALTER TABLE articles ADD COLUMN manual_optimization_disabled TINYINT(1) DEFAULT 0;

-- 3. 索引
ALTER TABLE articles ADD INDEX ix_articles_wechat_account (wechat_account_id);
ALTER TABLE articles ADD INDEX ix_articles_source (source_article_id);

-- 4. 验证
SELECT COUNT(*) as article_metrics_exists FROM information_schema.tables WHERE table_name = 'article_metrics';
SELECT COUNT(*) as article_columns_added FROM information_schema.columns WHERE table_name = 'articles' AND column_name IN ('latest_read_count', 'latest_quality_score', 'optimization_status');
