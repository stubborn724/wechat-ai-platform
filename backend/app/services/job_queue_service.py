"""Content job queue service — state machine, batch processing, and transitions."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import ContentJob, ContentJobArticle, ContentVersion

logger = logging.getLogger(__name__)

# ── State machine ──────────────────────────────────────────────────────────

VALID_TRANSITIONS = {
    "queue": ["pending"],
    "cancel": ["pending", "queued", "generating", "awaiting_review", "approved", "scheduled"],
    "pause": ["queued", "generating"],
    "resume": ["paused"],
    "approve": ["awaiting_review"],
    "reject": ["awaiting_review"],
    "schedule": ["approved"],
    "publish": ["approved", "scheduled"],
    "fail": ["pending", "queued", "generating"],
}


def validate_transition(job: ContentJob, action: str) -> str:
    """Validate that *action* is allowed for the job's current status.

    Returns the new status string, or raises ``ValueError``.
    """
    allowed_from = VALID_TRANSITIONS.get(action)
    if allowed_from is None:
        raise ValueError(f"Unknown action '{action}'")

    if job.status not in allowed_from:
        raise ValueError(
            f"Cannot '{action}' a job in status '{job.status}'. "
            f"Allowed from: {allowed_from}"
        )

    # Map action to new status
    status_map = {
        "queue": "queued",
        "cancel": "cancelled",
        "pause": "paused",
        "resume": "queued",
        "approve": "approved",
        "reject": "rejected",
        "schedule": "scheduled",
        "publish": "publishing",
        "fail": "failed",
    }
    return status_map[action]


def transition_job(db: Session, job_id: int, action: str) -> Optional[ContentJob]:
    """Execute a state transition on a content job.

    Returns the updated job, or ``None`` if not found.
    """
    job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
    if not job:
        return None

    new_status = validate_transition(job, action)
    if new_status:
        job.status = new_status
    db.commit()
    db.refresh(job)
    logger.info("ContentJob %d transitioned: %s -> %s", job_id, action, new_status)
    return job


# ── Batch processing ───────────────────────────────────────────────────────


def create_slot_articles(db: Session, job: ContentJob) -> List[ContentJobArticle]:
    """Create ContentJobArticle records based on the job's generation_config.

    If ``article_count`` is specified in generation_config, that many slots
    are created. Otherwise a single default slot is created.
    """
    config = job.generation_config or {}
    count = config.get("article_count", 1)

    slots = []
    for i in range(count):
        slot = ContentJobArticle(
            tenant_id=job.tenant_id,
            job_id=job.id,
            content_type=job.content_type or "article",
            sort_order=i,
            publish_domain="public",
            status="pending",
        )
        db.add(slot)
        slots.append(slot)

    db.commit()
    for s in slots:
        db.refresh(s)
    logger.info("Created %d slot articles for job %d", len(slots), job.id)
    return slots


def process_job_batch(db: Session, job: ContentJob) -> List[ContentVersion]:
    """Execute the full generation pipeline for a content job.

    For each article slot, runs the actual agent pipeline (title→outline→content→images)
    and stores results in ContentVersion records. Uses asyncio.run() to call async agents.
    """
    import asyncio
    from app.schemas.article import ArticleState, SelectedTitle
    from app.models.mysql_models import Article

    config = job.generation_config or {}
    count = config.get("article_count", 1)
    topic = job.topic or ""
    versions = []

    async def _run_slot(slot_index: int) -> dict:
        """Run the full agent pipeline for one article slot."""
        from app.services.article_agent_service import (
            agent1_generate_title_options,
            agent2_generate_outline,
            agent3_generate_content,
            agent4_analyze_image_requirements,
            agent5_generate_images,
            merge_images_into_content,
        )
        from app.config import settings

        state = ArticleState(
            task_id=f"job_{job.id}_{slot_index}",
            user_id=job.created_by or 0,
            topic=topic,
            style="default",
            footer_template=job.footer_template,
        )

        # Step 1: Title
        state = await agent1_generate_title_options(state)
        if not state.title_options:
            raise Exception("Title generation failed")
        first = state.title_options[0]
        state.title = SelectedTitle(main_title=first.main_title, sub_title=first.sub_title)

        # Step 2: Outline
        state = await agent2_generate_outline(state)

        # Step 3: Content
        state = await agent3_generate_content(state)

        # Step 4: Images
        state = await agent4_analyze_image_requirements(state)
        state = await agent5_generate_images(state)
        state = merge_images_into_content(state)

        return {
            "title": f"{first.main_title} - {first.sub_title}",
            "body_markdown": state.full_content or state.content or "",
            "summary": first.sub_title,
            "cover_url": next(
                (img.url for img in state.images if getattr(img, "position", None) == 1),
                None,
            ),
            "images": [img.url for img in state.images if img.url],
        }

    # Mark job as generating
    job.status = "generating"
    db.commit()

    for i in range(count):
        try:
            result = asyncio.run(_run_slot(i))

            version = ContentVersion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                version_number=i + 1,
                title=result["title"],
                body_markdown=result["body_markdown"],
                summary=result["summary"][:200] if result["summary"] else topic[:200],
                source="agent",
                created_by=job.created_by,
            )
            db.add(version)
            db.flush()

            # Save images to asset library
            if result["images"]:
                try:
                    from app.services.asset_archive_service import save_images_to_asset_library
                    asyncio.run(save_images_to_asset_library(
                        db, job.tenant_id, result["images"],
                    ))
                except Exception as arch_exc:
                    logger.warning("Slot %d asset archive failed: %s", i, arch_exc)

            versions.append(version)
            logger.info("Job %d slot %d generated: %s", job.id, i, result["title"][:60])

        except Exception as exc:
            logger.error("Slot %d processing failed for job %d: %s", i, job.id, exc)
            version = ContentVersion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                version_number=i + 1,
                title=topic,
                body_markdown="",
                summary=f"Failed: {str(exc)[:200]}",
                source="agent",
                created_by=job.created_by,
            )
            db.add(version)
            db.flush()
            versions.append(version)

    db.commit()
    logger.info("Processed job %d: %d versions created", job.id, len(versions))
    return versions
