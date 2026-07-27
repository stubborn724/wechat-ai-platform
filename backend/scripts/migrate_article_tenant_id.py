"""Article 表 tenant_id 回填和迁移脚本

将现有 Article 记录通过 ContentVersion → ContentJob 链路回填 tenant_id，
然后将 tenant_id 改为非空。
"""
import logging
from app.database import MysqlSessionLocal
from app.models.mysql_models import Article, ContentVersion, ContentJob

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_article_tenant_id():
    """为缺少 tenant_id 的 Article 回填"""
    db = MysqlSessionLocal()
    try:
        articles = db.query(Article).filter(Article.tenant_id.is_(None)).all()
        logger.info("Found %d articles without tenant_id", len(articles))

        filled = 0
        for article in articles:
            # Try: Article → ContentVersion → ContentJob → tenant_id
            version = (
                db.query(ContentVersion)
                .filter(ContentVersion.article_id == article.id)
                .first()
            )
            if version:
                job = db.query(ContentJob).filter(ContentJob.id == version.job_id).first()
                if job:
                    article.tenant_id = job.tenant_id
                    filled += 1
                    continue

            # Fallback: use ContentJob by task_id
            job = (
                db.query(ContentJob)
                .filter(ContentJob.idempotency_key.like(f"%{article.task_id}%"))
                .first()
            )
            if job:
                article.tenant_id = job.tenant_id
                filled += 1
                continue

            logger.warning("Article %d (task_id=%s) could not be mapped to a tenant, skipping", article.id, article.task_id)

        db.commit()
        logger.info("Backfilled tenant_id for %d articles", filled)

        # Remaining null tenant_id count
        remaining = db.query(Article).filter(Article.tenant_id.is_(None)).count()
        if remaining > 0:
            logger.warning("%d articles still have null tenant_id", remaining)
        else:
            logger.info("All articles have tenant_id — ready to make column non-nullable")

    finally:
        db.close()


if __name__ == "__main__":
    backfill_article_tenant_id()
