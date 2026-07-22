"""Unified scheduled task executor — replaces publish_tasks.check_publish_plans + imitation_tasks.poll_due_imitation_tasks"""

import logging
import uuid
from datetime import date, datetime

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask, ContentJob

logger = logging.getLogger(__name__)


@celery_app.task
def check_scheduled_tasks():
    """Periodic task: check scheduled tasks that need to execute now.

    Replaces both check_publish_plans and poll_due_imitation_tasks.
    For each due task, creates a ContentJob that flows through the
    existing generation pipeline.
    """
    db = MysqlSessionLocal()
    try:
        # Use Asia/Shanghai timezone for time comparison (user sets times in local TZ)
        from datetime import timezone as tz
        import zoneinfo
        shanghai_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now_shanghai = datetime.now(shanghai_tz)
        today = now_shanghai.date()
        day_of_week = today.weekday()
        current_hour_min = f"{now_shanghai.hour:02d}:{now_shanghai.minute:02d}"

        tasks = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.is_active == True,
                ScheduledTask.day_of_week.in_([day_of_week, -1]),
            )
            .all()
        )

        triggered = 0
        for task in tasks:
            if not task.publish_times:
                continue

            for pub_time in task.publish_times:
                if pub_time and pub_time <= current_hour_min:
                    # Dedup: check if already created today
                    existing = (
                        db.query(ContentJob)
                        .filter(
                            ContentJob.idempotency_key == f"scheduled_{task.id}_{today.isoformat()}",
                        )
                        .first()
                    )
                    if existing:
                        logger.debug("Task %d already triggered today, skipping", task.id)
                        continue

                    _create_job_for_task(db, task)
                    triggered += 1
                    task.total_generated = (task.total_generated or 0) + 1
                    task.last_run_at = now_shanghai
                    break  # One job per task per check

        if triggered:
            db.commit()

        logger.info("Scheduled tasks: %d due tasks, %d jobs created", len(tasks), triggered)
        return {"tasks_checked": len(tasks), "jobs_created": triggered}

    except Exception as exc:
        logger.error("Scheduled task check failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def _create_job_for_task(db, task: ScheduledTask):
    """Create a ContentJob from a scheduled task and enqueue it for processing."""
    config = {
        "writing_mode": task.writing_mode,
        "feed_source_ids": task.feed_source_ids,
        "style": task.style,
        "knowledge_base_ids": task.knowledge_base_ids,
        "article_slots": task.article_slots,
        "article_count": task.articles_per_day,
        "public_count": task.public_count,
        "private_count": task.private_count,
        "scheduled_task_id": task.id,
    }

    topic = task.topic or task.name

    job = ContentJob(
        tenant_id=task.tenant_id,
        account_id=task.account_id,
        status="queued",  # Directly queued so poll_queued_jobs picks it up
        version=1,
        topic=topic,
        content_type="article",
        approval_mode=task.approval_mode,
        idempotency_key=f"scheduled_{task.id}_{date.today().isoformat()}",
        created_by=task.created_by,
        generation_config=config,
        footer_template=task.footer_template,
    )
    db.add(job)
    logger.info("Created scheduled job for task %d (mode=%s): %s", task.id, task.writing_mode, topic[:60])
