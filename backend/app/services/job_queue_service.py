"""Content job queue service — state machine, batch processing, and transitions."""

import asyncio
import json
import logging
import re
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
    "approve": ["awaiting_review", "generating"],
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
            style=config.get("style", "default"),
            footer_template=job.footer_template,
        )

        # ── Writing mode setup ──────────────────────────────────────
        writing_mode = config.get("writing_mode", "free")
        kb_ids = config.get("knowledge_base_ids")

        if writing_mode == "feed":
            feed_source_ids = config.get("feed_source_ids", [])
            if feed_source_ids:
                try:
                    from app.models.mysql_models import FeedSourceArticle, FeedSource

                    # Load style profiles from feed sources → set on state
                    sources = (
                        db.query(FeedSource)
                        .filter(FeedSource.id.in_(feed_source_ids))
                        .all()
                    )
                    for s in sources:
                        if s.style_profile:
                            state.style_profile = s.style_profile
                            break  # Use first source's style profile

                    # Load reference articles → set on state
                    articles = (
                        db.query(FeedSourceArticle)
                        .filter(
                            FeedSourceArticle.feed_source_id.in_(feed_source_ids),
                            FeedSourceArticle.body_markdown.isnot(None),
                        )
                        .order_by(FeedSourceArticle.published_at.desc())
                        .limit(3)
                        .all()
                    )
                    if articles:
                        ref_texts = []
                        for article in articles:
                            body = article.body_markdown or ""
                            # Strip watermarks, contacts, image descriptions from reference
                            body = re.sub(r'[（(][^)）]*(?:水印|微信|电话|手机|TEL|公众号|扫码)[^)）]*[）)]', '', body)
                            body = re.sub(r'右下角带水印[""「」].*?[""」]', '', body)
                            # Strip [IMAGE:...] markers
                            body = re.sub(r'\[IMAGE:[^\]]*\]', '', body)
                            # Strip photography/image description lines (e.g. "45度俯拍暖光下的...")
                            body = re.sub(r'^.*?(?:45度|俯拍|仰拍|侧拍|微距|特写|暖光|逆光|侧光|打光|布光).*?(?:场景|效果|展示|呈现|体现).*?$', '', body, flags=re.MULTILINE)
                            body = re.sub(r'^.*?(?:拍摄于|摄于|摄影|摄影师|镜头|焦距|光圈|快门).*?$', '', body, flags=re.MULTILINE)
                            if body and len(body) > 100:
                                ref_texts.append(f"## {article.title or '参考文章'}\n\n{body[:300]}")
                        if ref_texts:
                            state.reference_articles = ref_texts

                    # KB context for feed mode
                    if kb_ids:
                        from app.database import get_pg_db
                        from app.services.knowledge_base_service import search_knowledge_base

                        pg_db = next(get_pg_db())
                        try:
                            contexts = []
                            for kb_id in kb_ids:
                                results = search_knowledge_base(pg_db, kb_id, topic, top_k=3)
                                for r in (results or []):
                                    contexts.append(f"[知识库 {kb_id}] {r.get('content', '')}")
                            if contexts:
                                state.kb_context = "\n\n".join(contexts)
                        finally:
                            pg_db.close()

                except Exception as feed_exc:
                    logger.warning("Feed source setup failed (fallback to free): %s", feed_exc)

        elif writing_mode == "kb" and kb_ids:
            try:
                from app.database import get_pg_db
                from app.services.knowledge_base_service import search_knowledge_base

                pg_db = next(get_pg_db())
                try:
                    contexts = []
                    for kb_id in kb_ids:
                        results = search_knowledge_base(pg_db, kb_id, topic, top_k=3)
                        for r in (results or []):
                            contexts.append(f"[知识库 {kb_id}] {r.get('content', '')}")
                    if contexts:
                        state.kb_context = "\n\n".join(contexts)
                        state.topic = f"{topic}\n\n## 📚 参考资料\n{state.kb_context}".replace("{", "{{").replace("}", "}}")
                finally:
                    pg_db.close()
            except Exception as kb_exc:
                logger.warning("Knowledge base search failed: %s", kb_exc)

        # ── End writing mode setup ──────────────────────────────────

        # Step 1: Title
        state = await agent1_generate_title_options(state)
        if not state.title_options:
            raise Exception("Title generation failed")
        first = state.title_options[0]
        state.title = SelectedTitle(main_title=first.main_title, sub_title=first.sub_title)

        # Step 2: Outline
        state = await agent2_generate_outline(state)

        # Step 3: Content — extract image keywords BEFORE merge to use for cleanup
        state = await agent3_generate_content(state)
        raw_content = state.content or ""
        image_keywords_from_content = re.findall(r'keywords=([^,\]]+)', raw_content)

        # Step 4: Images (with fallback placeholders)
        try:
            state = await agent4_analyze_image_requirements(state)
            state = await agent5_generate_images(state)
        except Exception as img_exc:
            logger.warning("Image generation failed for job %d slot %d: %s", job.id, slot_index, img_exc)
        # Merge images — if none available, replace [IMAGE:] with placeholders
        if not state.images:
            import secrets
            has_image_markers = bool(re.search(r'\[IMAGE:', raw_content))
            if has_image_markers:
                # Replace existing [IMAGE:] markers with placeholder images
                def _ph(m: re.Match) -> str:
                    pos = re.search(r'position=(\d+)', m.group(1))
                    idx = int(pos.group(1)) if pos else 1
                    return f'<img src="https://picsum.photos/seed/{secrets.token_hex(4)}{idx}/800/400" style="width:100%;border-radius:8px;margin:16px 0;" />'
                state.full_content = re.sub(r'\[IMAGE:(.*?)\]', _ph, raw_content)
            else:
                # No [IMAGE:] markers at all — inject placeholders at section breaks
                logger.info("No [IMAGE:] markers found in job %d slot %d — injecting placeholders", job.id, slot_index)
                lines = raw_content.split("\n")
                result_lines = []
                img_count = 0
                for i, line in enumerate(lines):
                    result_lines.append(line)
                    # Inject after headings or every ~5 paragraphs
                    is_heading = line.strip().startswith('##') or line.strip().startswith('**')
                    if is_heading and img_count < 6 and i > 0:
                        img_count += 1
                        result_lines.append(
                            f'\n<img src="https://picsum.photos/seed/{secrets.token_hex(4)}{img_count}/800/400" '
                            f'style="width:100%;border-radius:8px;margin:16px 0;" />\n'
                        )
                state.full_content = "\n".join(result_lines)
            # Append footer if configured
            if job.footer_template:
                footer = job.footer_template.strip()
                if footer:
                    state.full_content = f"{state.full_content}\n\n---\n\n{footer}"
        else:
            state = merge_images_into_content(state)

        # Step 5: Generate AI cover image
        cover_url = next(
            (img.url for img in state.images if getattr(img, "position", None) == 1),
            None,
        )
        if not cover_url and settings.dashscope_api_key:
            try:
                from app.services.wanxiang_service import WanxiangImageService
                main_title_text = first.main_title
                cover_prompt = f"公众号文章封面图：{main_title_text}。扁平化设计，简洁大气，适合社交媒体传播。"
                ws = WanxiangImageService()
                ai_cover = await ws.generate_image(cover_prompt, size="1024*1024")
                if ai_cover:
                    cover_url = ai_cover
            except Exception as cover_err:
                logger.warning("Cover image generation failed for job %d slot %d: %s", job.id, slot_index, cover_err)

        # Post-generation cleanup: strip image description lines using pre-extracted keywords
        raw_body = state.full_content or raw_content
        cleaned_body = _strip_image_descriptions(raw_body, image_keywords_from_content)

        return {
            "title": f"{first.main_title} - {first.sub_title}",
            "body_markdown": cleaned_body,
            "summary": first.sub_title,
            "cover_url": cover_url,
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
                model_metadata={"cover_url": result.get("cover_url")} if result.get("cover_url") else None,
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


def _strip_image_descriptions(text: str, pre_extracted_keywords: list | None = None) -> str:
    """Post-processing: remove photography/image description language from article body.

    Strategy:
    1. Use pre-extracted IMAGE keywords (captured before merge) → remove matching body lines
    2. Remove lines with 2+ photography terms
    3. Remove any remaining [IMAGE:] markers

    Note: lines containing markdown image syntax ![alt](url) are ALWAYS preserved.
    """
    if not text:
        return text

    # Step 1: Collect keywords from pre-extracted, [IMAGE:] markers, AND markdown alt text
    image_keywords = list(pre_extracted_keywords or [])
    image_keywords.extend(re.findall(r'keywords=([^,\]]+)', text))
    image_keywords.extend(re.findall(r'!\[([^\]]+)\]\([^)]+\)', text))
    text = re.sub(r'\[IMAGE:[^\]]*\]', '', text)

    lines = text.split("\n")
    photo_kw = ['俯拍', '仰拍', '侧拍', '微距', '特写', '近景', '远景', '中景',
                '暖光', '逆光', '侧光', '顶光', '底光', '打光', '布光',
                '景深', '光圈', '快门', '45度']

    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            cleaned.append(line)
            continue

        # ALWAYS preserve markdown image lines (![alt](url))
        if re.match(r'^!\[.*\]\(.*\)$', s):
            cleaned.append(line)
            continue

        # Step 2: Remove if line matches any IMAGE keyword phrase (AI's own description)
        if image_keywords:
            skip = False
            for kw in image_keywords:
                if len(kw) >= 6 and kw in s:
                    skip = True
                    break
            if skip:
                continue

        # Step 3: Remove lines with 2+ photography terms
        if sum(1 for kw in photo_kw if kw in s) >= 2:
            continue

        # Step 4: Remove watermark lines
        if re.search(r'(?:右下角|左下角|右上角|左上角).*(?:水印|文字|标志|logo)', s, re.IGNORECASE):
            continue

        cleaned.append(line)

    return "\n".join(cleaned)
