"""Periodic tasks for feed source fetching and layout analysis."""

import json
import logging

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import FeedSource, FeedSourceArticle

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
                result = asyncio.run(fetch_source(db, source.id, tenant_id=source.tenant_id))
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


@celery_app.task
def analyze_pending_layouts():
    """Batch-process feed articles that need layout analysis.

    Runs after feed fetching. Picks up articles with ``body_html`` but
    no layout analysis result, calls the LLM to extract their structural
    template, and saves the result into ``analysis``.
    """
    import asyncio
    from app.services.layout_analysis_service import analyze_feed_article_layout

    db = MysqlSessionLocal()
    try:
        articles = (
            db.query(FeedSourceArticle)
            .filter(
                FeedSourceArticle.body_html.isnot(None),
                FeedSourceArticle.body_html != "",
                FeedSourceArticle.analysis.is_(None),
            )
            .order_by(FeedSourceArticle.id.desc())
            .limit(20)
            .all()
        )

        if not articles:
            return {"analyzed": 0}

        analyzed_count = 0
        for article in articles:
            try:
                meta = asyncio.run(analyze_feed_article_layout(
                    html=article.body_html,
                    markdown=article.body_markdown or "",
                    title=article.title or "",
                ))
                article.analysis = json.loads(meta.model_dump_json())
                db.commit()
                analyzed_count += 1

                if analyzed_count % 5 == 0:
                    logger.info("Layout analysis: %d / %d articles", analyzed_count, len(articles))

            except Exception as exc:
                logger.warning("Layout analysis failed for article %d: %s", article.id, exc)
                # Mark as failed so we don't retry indefinitely
                article.analysis = {
                    "schema_version": "1.0",
                    "layout_status": "failed",
                    "layout_error": str(exc)[:300],
                }
                db.commit()

        return {"analyzed": analyzed_count, "total_pending": len(articles)}

    finally:
        db.close()
