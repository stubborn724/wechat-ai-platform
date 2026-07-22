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
        # Check scheduled tasks every 15 minutes
        "check-scheduled-tasks-every-15-minutes": {
            "task": "app.tasks.scheduled_task_executor.check_scheduled_tasks",
            "schedule": 900.0,
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
    },
)

# Auto-discover tasks so Celery worker can find them
celery_app.autodiscover_tasks(["app.tasks"])
