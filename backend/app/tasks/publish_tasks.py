"""Periodic tasks for scheduled article publishing."""

import logging
from datetime import date, datetime
from typing import Optional

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import PublishPlan, ContentJob, Article, WeChatAccount

logger = logging.getLogger(__name__)


@celery_app.task
def check_publish_plans():
    """Periodic task: check publish plans that need to execute now.

    Looks at the day of week and scheduled publish times,
    then creates content jobs for any plans that are due.
    """
    db = MysqlSessionLocal()
    try:
        today = date.today()
        day_of_week = today.weekday()  # 0=Monday, 6=Sunday
        now = datetime.utcnow()
        current_hour_min = f"{now.hour:02d}:{now.minute:02d}"

        plans = (
            db.query(PublishPlan)
            .filter(
                PublishPlan.day_of_week == day_of_week,
                PublishPlan.is_active == True,
            )
            .all()
        )

        triggered = 0
        for plan in plans:
            if not plan.publish_times:
                continue

            for pub_time in plan.publish_times:
                if pub_time and pub_time <= current_hour_min:
                    # Check if we already created a job for this plan+time+today
                    existing = (
                        db.query(ContentJob)
                        .filter(
                            ContentJob.account_id == plan.account_id,
                            ContentJob.created_at >= datetime(today.year, today.month, today.day),
                        )
                        .first()
                    )
                    if existing:
                        logger.debug("Already created job for plan %d today, skipping", plan.id)
                        continue

                    _create_job_for_plan(db, plan)
                    triggered += 1
                    break  # One job per plan per check

        if triggered:
            db.commit()
            logger.info("Triggered %d publish jobs from plans", triggered)

        return {"plans_checked": len(plans), "jobs_created": triggered}

    except Exception as exc:
        logger.error("Publish plan check failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def _create_job_for_plan(db, plan: PublishPlan):
    """Create a content job for a publish plan."""
    import uuid

    job = ContentJob(
        tenant_id=plan.tenant_id,
        account_id=plan.account_id,
        status="pending",
        version=1,
        topic=f"定时发布 - {plan.account_id} - {date.today().isoformat()}",
        content_type="article",
        approval_mode="auto",
        idempotency_key=f"plan_{plan.id}_{date.today().isoformat()}_{uuid.uuid4().hex[:8]}",
        created_by=None,
        generation_config={
            "plan_id": plan.id,
            "article_slots": plan.article_slots,
            "public_count": plan.public_count,
            "private_count": plan.private_count,
        },
    )
    db.add(job)
    logger.info("Created scheduled job for plan %d (account=%s)",
                plan.id, plan.account_id)
