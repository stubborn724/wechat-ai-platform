"""Celery app instance for the WeChat AI Platform."""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "wechat_ai",
    broker=settings.celery_broker_url or "redis://localhost:6379/1",
    backend=settings.celery_result_backend or "redis://localhost:6379/1",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    worker_pool="solo",  # Windows compatibility
    beat_schedule={
        # Fetch all active feed sources every 30 minutes
        "fetch-feeds-every-30-minutes": {
            "task": "app.tasks.feed_tasks.fetch_all_feeds",
            "schedule": 1800.0,
        },
        # Check scheduled tasks every minute
        "check-scheduled-tasks-every-minute": {
            "task": "app.tasks.scheduled_task_executor.check_scheduled_tasks",
            "schedule": 60.0,
        },
        # Poll queued content jobs every minute
        "poll-queued-jobs-every-minute": {
            "task": "app.tasks.job_tasks.poll_queued_jobs",
            "schedule": 60.0,
        },
        # Clean up old unused assets daily
        "cleanup-old-assets-daily": {
            "task": "app.tasks.job_tasks.cleanup_old_assets",
            "schedule": 86400.0,
        },
        # Poll publishing articles every 2 minutes to get msg_data_id
        "poll-publishing-articles-every-2-minutes": {
            "task": "app.tasks.job_tasks.poll_publishing_articles",
            "schedule": 120.0,
        },
        # Sync comments for published articles every 30 minutes
        "sync-comments-every-30-minutes": {
            "task": "app.tasks.job_tasks.sync_comments_for_published_articles",
            "schedule": 1800.0,
        },
        # Poll due imitation tasks (backward compatibility)
        "poll-due-imitation-tasks-every-15-minutes": {
            "task": "app.tasks.imitation_tasks.poll_due_imitation_tasks",
            "schedule": 900.0,
        },
        # Sync article reading metrics daily at 02:00
        "sync-article-metrics-daily": {
            "task": "app.tasks.metrics_tasks.schedule_article_metrics_sync",
            "schedule": 86400.0,
        },
        # Retry failed metrics sync hourly
        "retry-failed-metrics-sync-hourly": {
            "task": "app.tasks.metrics_tasks.retry_failed_metrics_sync",
            "schedule": 3600.0,
        },
        # Batch evaluate unrated articles daily at 03:00
        "batch-evaluate-articles-daily": {
            "task": "app.tasks.quality_tasks.batch_evaluate_articles",
            "schedule": 86400.0,
        },
        # Check optimization candidates every 30 minutes
        "check-optimization-candidates-every-30-minutes": {
            "task": "app.tasks.optimization_tasks.check_optimization_candidates",
            "schedule": 1800.0,
        },
        # Analyze article layout structure for unanalyzed feed articles
        "analyze-pending-layouts-every-10-minutes": {
            "task": "app.tasks.feed_tasks.analyze_pending_layouts",
            "schedule": 600.0,
        },
    },
)

# Import all task modules explicitly so Celery can register @celery_app.task decorated functions
import app.tasks.feed_tasks  # noqa: F401
import app.tasks.job_tasks  # noqa: F401
import app.tasks.content_tasks  # noqa: F401
import app.tasks.metrics_tasks  # noqa: F401
import app.tasks.quality_tasks  # noqa: F401
import app.tasks.optimization_tasks  # noqa: F401
import app.tasks.imitation_tasks  # noqa: F401
import app.tasks.scheduled_task_executor  # noqa: F401
