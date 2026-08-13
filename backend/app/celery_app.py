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
    # 定时文章会占用 Worker 较长时间，单独路由到 scheduled 队列后由专用
    # Worker 消费，避免图片生成阻塞普通队列和其他定时检查任务。
    task_routes={
        "app.tasks.scheduled_task_executor.execute_scheduled_article": {
            "queue": "scheduled",
        },
    },
    # 生产者与 Redis 的连接短暂抖动时，Celery 发布动作自动重试；数据库中的
    # queued 记录仍是最终兜底，Beat 会在消息长期未被认领时再次补投。
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 5,
        "interval_start": 0.5,
        "interval_step": 1,
        "interval_max": 5,
    },
    # 图片任务可能超过 Redis 默认的可见性时间。消息只有在 Worker 确认后才会
    # 移除，延长可见性窗口可以避免正常长任务被 Redis 提前重复投递。
    broker_transport_options={
        "visibility_timeout": 2 * 60 * 60,
    },
    # 定时文章通常包含多次外部 API 调用。延迟确认消息并在 Worker 丢失时让
    # Broker 重投，避免数据库已经写成 running 但 Celery 消息永久消失。
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
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
        # TaGeAI 状态回调采用 outbox；高频轻量轮询让 Gateway 无需等待分钟级补偿，失败仍由
        # outbox 保存事件并进行有限指数退避。
        "deliver-tageai-callback-outbox-every-15-seconds": {
            "task": "app.tasks.job_tasks.deliver_tageai_callback_outbox",
            "schedule": 15.0,
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
