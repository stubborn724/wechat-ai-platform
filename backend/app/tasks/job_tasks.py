"""Celery tasks for content job queue processing and asset cleanup."""

import logging

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ContentJob
from app.services.job_queue_service import process_job_batch, transition_job

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_content_job(self, job_id: int):
    """Process a queued content job through the generation pipeline."""
    db = MysqlSessionLocal()
    try:
        job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        if job.status != "queued":
            return {"error": f"Job {job_id} is not in queued state (status={job.status})"}

        # Run the generation pipeline
        versions = process_job_batch(db, job)

        # Auto-approve if configured
        if job.approval_mode == "auto":
            transition_job(db, job_id, "approve")
            logger.info("Job %d auto-approved after generation", job_id)
        else:
            transition_job(db, job_id, "approve")
            logger.info("Job %d completed, awaiting review", job_id)

        return {
            "job_id": job_id,
            "versions_created": len(versions),
            "status": job.status,
        }

    except Exception as exc:
        logger.error("Job %d processing failed: %s", job_id, exc)
        try:
            transition_job(db, job_id, "fail")
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task
def poll_queued_jobs():
    """Beat task: Look for queued jobs and dispatch them to the worker."""
    db = MysqlSessionLocal()
    try:
        jobs = (
            db.query(ContentJob)
            .filter(ContentJob.status == "queued")
            .order_by(ContentJob.created_at.asc())
            .limit(5)
            .all()
        )
        for job in jobs:
            process_content_job.delay(job.id)
            logger.info("Dispatched job %d to worker", job.id)
        return {"dispatched": len(jobs)}
    finally:
        db.close()


@celery_app.task
def cleanup_old_assets():
    """Daily task: identify old unused assets for archival."""
    db = MysqlSessionLocal()
    try:
        from app.models.mysql_models import Asset
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        old_unused = (
            db.query(Asset)
            .filter(
                Asset.created_at < cutoff,
                Asset.usage_count == 0,
            )
            .count()
        )
        logger.info("Asset cleanup: %d assets eligible for archive (90+ days, 0 usage)", old_unused)
        return {"eligible_for_archive": old_unused}
    finally:
        db.close()
