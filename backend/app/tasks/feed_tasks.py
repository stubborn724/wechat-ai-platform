"""Periodic tasks for feed source fetching."""

import logging

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import FeedSource

logger = logging.getLogger(__name__)


@celery_app.task
def fetch_all_feeds():
    """Periodic task: fetch all active feed sources."""
    import asyncio
    from app.services.feed_service import fetch_source

    db = MysqlSessionLocal()
    try:
        sources = (
            db.query(FeedSource)
            .filter(FeedSource.is_active == True)
            .all()
        )
        results = []
        for source in sources:
            try:
                result = asyncio.run(fetch_source(db, source.id))
                results.append({
                    "source_id": source.id,
                    "source_name": source.name,
                    "articles_saved": result.get("articles_saved", 0),
                })
                logger.info("Fetched feed %s: %d articles saved",
                            source.name, result.get("articles_saved", 0))
            except Exception as exc:
                logger.error("Failed to fetch feed %s: %s", source.name, exc)
                results.append({
                    "source_id": source.id,
                    "source_name": source.name,
                    "error": str(exc),
                })

        return {"fetched": len(sources), "results": results}

    finally:
        db.close()
