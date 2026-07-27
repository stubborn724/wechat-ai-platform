"""阅读指标同步 Celery 任务"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import Article, ArticleMetrics, TaskExecutionLog

logger = logging.getLogger(__name__)


def _log_execution(db, task_name: str, article_id: int = None,
                   account_id: int = None, status: str = "running",
                   error: str = None):
    """记录任务执行日志"""
    log = TaskExecutionLog(
        task_name=task_name,
        task_id=f"{task_name}_{article_id}_{date.today().isoformat()}",
        article_id=article_id,
        account_id=account_id,
        status=status,
        error_message=error,
    )
    db.add(log)


@celery_app.task
def sync_single_article_metrics(article_id: int, metric_date: str = None):
    """同步单篇文章的阅读指标

    Args:
        article_id: 文章 ID
        metric_date: 指标日期 YYYY-MM-DD，默认昨天
    """
    if metric_date is None:
        metric_date = (date.today() - timedelta(days=1)).isoformat()

    db = MysqlSessionLocal()
    try:
        article = db.query(Article).filter(Article.id == article_id).first()
        if not article:
            logger.warning("Article %d not found, skipping metrics sync", article_id)
            return {"error": "article_not_found"}

        if not article.msg_data_id or not article.wechat_account_id:
            logger.info("Article %d has no msg_data_id or account, skipping", article_id)
            return {"error": "missing_wechat_info"}

        async def _do_sync():
            from app.services.wechat_metrics_service import get_metrics_service
            svc = await get_metrics_service(db, article.wechat_account_id)
            raw = await svc.fetch_article_metrics(
                article.msg_data_id, metric_date, metric_date
            )
            return svc.normalize_metrics(raw, article_id, metric_date)

        norm = asyncio.run(_do_sync())

        if norm:
            # upsert: 同一天同一篇文章只保留一条
            existing = db.query(ArticleMetrics).filter(
                ArticleMetrics.article_id == article_id,
                ArticleMetrics.metric_date == metric_date,
            ).first()

            if existing:
                for k, v in norm.items():
                    if k not in ("article_id", "metric_date") and v is not None:
                        setattr(existing, k, v)
                existing.sync_status = "success"
            else:
                norm["sync_status"] = "success"
                db.add(ArticleMetrics(**norm))

            # 更新 Article 缓存字段
            article.latest_read_count = norm.get("read_count", article.latest_read_count or 0)
            article.latest_like_count = norm.get("like_count", article.latest_like_count or 0)
            article.latest_share_count = norm.get("share_count", article.latest_share_count or 0)
            article.latest_comment_count = norm.get("comment_count", article.latest_comment_count or 0)
            article.latest_fav_count = norm.get("add_to_fav_count", article.latest_fav_count or 0)
            article.metrics_updated_at = datetime.utcnow()

            _log_execution(db, "sync_single_article_metrics", article_id,
                          article.wechat_account_id, "success")
            db.commit()
            logger.info("Synced metrics for article %d on %s: read=%s",
                       article_id, metric_date, norm.get("read_count"))
            return {"article_id": article_id, "status": "success"}
        else:
            _log_execution(db, "sync_single_article_metrics", article_id,
                          article.wechat_account_id, "failed", "empty_response")
            db.commit()
            return {"article_id": article_id, "status": "empty"}

    except Exception as exc:
        logger.error("Metrics sync failed for article %d: %s", article_id, exc)
        db.rollback()
        _log_execution(db, "sync_single_article_metrics", article_id,
                      getattr(article, 'wechat_account_id', None) if 'article' in dir() else None,
                      "failed", str(exc))
        db.commit()
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task
def schedule_article_metrics_sync():
    """Beat 任务：扫描需要同步的文章并分发"""
    db = MysqlSessionLocal()
    try:
        metric_date = (date.today() - timedelta(days=1)).isoformat()

        articles = (
            db.query(Article)
            .filter(
                Article.status == "published",
                Article.msg_data_id.isnot(None),
                Article.msg_data_id != "",
                Article.wechat_account_id.isnot(None),
            )
            .order_by(Article.id.desc())
            .all()
        )

        dispatched = 0
        for article in articles:
            sync_single_article_metrics.delay(article.id, metric_date)
            dispatched += 1

        logger.info("Scheduled metrics sync for %d articles on %s", dispatched, metric_date)
        return {"dispatched": dispatched, "metric_date": metric_date}

    except Exception as exc:
        logger.error("schedule_article_metrics_sync failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task
def retry_failed_metrics_sync():
    """补偿任务：重试之前失败的指标同步"""
    db = MysqlSessionLocal()
    try:
        # 查找最近 3 天失败的记录
        cutoff = (date.today() - timedelta(days=3)).isoformat()
        failed_logs = (
            db.query(TaskExecutionLog)
            .filter(
                TaskExecutionLog.task_name == "sync_single_article_metrics",
                TaskExecutionLog.status == "failed",
                TaskExecutionLog.created_at >= cutoff,
            )
            .limit(50)
            .all()
        )

        retried = 0
        for log in failed_logs:
            if log.article_id:
                metric_date = (date.today() - timedelta(days=1)).isoformat()
                sync_single_article_metrics.delay(log.article_id, metric_date)
                retried += 1
                log.status = "retried"

        db.commit()
        logger.info("Retried %d failed metrics syncs", retried)
        return {"retried": retried}

    except Exception as exc:
        logger.error("retry_failed_metrics_sync failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()
