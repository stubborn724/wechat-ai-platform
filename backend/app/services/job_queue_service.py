"""Content job queue service — state machine, batch processing, and transitions."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.integrations.tageai.generation_context import (
    TageAiGenerationContextError,
    apply_tageai_generation_context,
)
from app.models.mysql_models import ContentJob, ContentJobArticle, ContentVersion, PublishAttempt

logger = logging.getLogger(__name__)

# ── ContentJob state machine ──────────────────────────────────────────────

VALID_TRANSITIONS = {
    "queue": ["pending"],
    "cancel": ["pending", "queued", "dispatching", "generating", "awaiting_review", "approved", "scheduled"],
    "pause": ["queued", "dispatching", "generating"],
    "resume": ["paused"],
    "approve": ["queued", "awaiting_review", "generating"],
    "reject": ["awaiting_review"],
    "schedule": ["approved"],
    "publish": ["approved", "scheduled", "partially_published"],
    "partial_success": ["publishing"],
    "complete": ["publishing", "partially_published"],
    "fail": ["pending", "queued", "dispatching", "generating", "publishing"],
}

# ``dispatching`` 是数据库中的短暂领取状态，不代表内容生成已经开始。它把 Beat 的发现与
# Celery 消息投递之间的窗口固化下来，使多个 Beat 实例或重复消息无法并发执行同一 ContentJob。
JOB_DISPATCHING_STATUS = "dispatching"
JOB_DISPATCH_STALE_SECONDS = 300

# ── PublishAttempt state machine ──────────────────────────────────────────

PUBLISH_ATTEMPT_TRANSITIONS = {
    "pending": ["queued"],
    "queued": ["publishing", "failed"],
    "publishing": ["success", "failed", "retrying"],
    "retrying": ["publishing", "failed"],
    "success": [],
    "failed": [],
}


class JobCancellationRequested(RuntimeError):
    """Worker 在安全阶段边界发现持久化取消请求时抛出的控制流异常。

    这个异常不表示生成失败，也不应进入通用 ``fail`` 状态机。它专门让调用方回滚尚未
    投递的临时对象，并把 ContentJob 收敛到真正的 ``cancelled``，从而区分“请求取消”
    与“已停止所有后续副作用”。
    """


def raise_if_job_cancellation_requested(db: Session, job_id: int) -> None:
    """读取数据库中的最新取消标志，避免 Worker 只依赖陈旧 ORM 对象。

    取消请求来自另一个 HTTP 事务；因此不能检查当前 ``job.status`` 实例属性。只查询
    ``status`` 列可以绕开 identity map 的旧快照，在模型调用、图片处理和投递前以低
    成本确认是否必须停止。
    """

    current_status = (
        db.query(ContentJob.status)
        .filter(ContentJob.id == job_id)
        .scalar()
    )
    if current_status in {"cancel_requested", "cancelled"}:
        raise JobCancellationRequested(f"ContentJob {job_id} cancellation requested")


def claim_queued_job_for_dispatch(db: Session, job_id: int) -> Optional[ContentJob]:
    """原子领取一条已排队任务，供 Beat 或 HTTP 入口投递 Celery 消息。

    ``SELECT`` 后再写状态会让多个 Beat 同时选中同一条任务。这里使用带状态条件的单条
    ``UPDATE``，只有实际把 ``queued`` 改为 ``dispatching`` 的调用方才能发送消息。领取信息
    复用 ``updated_at``，派发进程崩溃时由 :func:`recover_stale_dispatch_claims` 重新开放任务。
    """

    now = datetime.now(timezone.utc)
    updated = (
        db.query(ContentJob)
        .filter(ContentJob.id == job_id, ContentJob.status == "queued")
        .update({ContentJob.status: JOB_DISPATCHING_STATUS, ContentJob.updated_at: now}, synchronize_session=False)
    )
    db.commit()
    if updated != 1:
        return None
    return db.query(ContentJob).filter(ContentJob.id == job_id).first()


def claim_dispatched_job_for_execution(db: Session, job_id: int) -> Optional[ContentJob]:
    """原子领取已派发任务进入生成阶段，重复 Celery 消息只能有一个胜出。

    兼容旧版本在状态尚为 ``queued`` 时就投递的消息：两种状态都允许被第一个 Worker 领取，
    但条件更新保证其余副本读取到 ``generating`` 后直接退出，不会重复调用模型或公众号接口。
    """

    now = datetime.now(timezone.utc)
    updated = (
        db.query(ContentJob)
        .filter(ContentJob.id == job_id, ContentJob.status.in_(("queued", JOB_DISPATCHING_STATUS)))
        .update({ContentJob.status: "generating", ContentJob.updated_at: now}, synchronize_session=False)
    )
    db.commit()
    if updated != 1:
        return None
    return db.query(ContentJob).filter(ContentJob.id == job_id).first()


def release_dispatch_claim(db: Session, job_id: int) -> bool:
    """Celery 发布失败时释放仍未被 Worker 领取的任务，交给下一轮 Beat 安全重试。"""

    updated = (
        db.query(ContentJob)
        .filter(ContentJob.id == job_id, ContentJob.status == JOB_DISPATCHING_STATUS)
        .update({ContentJob.status: "queued", ContentJob.updated_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return updated == 1


def recover_stale_dispatch_claims(db: Session, stale_after_seconds: int = JOB_DISPATCH_STALE_SECONDS) -> int:
    """恢复派发进程异常留下的过期领取，避免任务永久停在 ``dispatching``。

    Worker 一旦成功领取会立即改为 ``generating``，因此只回收超过保守窗口仍未被领取的记录。
    回收动作不触碰取消或终态任务，后续 Beat 会再次通过原子领取流程投递消息。
    """

    deadline = datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))
    recovered = (
        db.query(ContentJob)
        .filter(ContentJob.status == JOB_DISPATCHING_STATUS, ContentJob.updated_at < deadline)
        .update({ContentJob.status: "queued", ContentJob.updated_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return recovered


def next_content_version_number(db: Session, job: ContentJob) -> int:
    """为同一内容任务分配下一个不可冲突的版本号。

    内容任务允许在外部依赖恢复后重试，失败版本仍需保留用于审计，不能删除后重用其编号。
    因此每次生成都从数据库中该任务的最高版本继续递增，而不是固定从 1 开始。该查询与
    ``content_versions(job_id, version_number)`` 唯一约束共同保证恢复任务不会因历史记录失败。
    """

    latest_version = (
        db.query(ContentVersion)
        .filter(
            ContentVersion.job_id == job.id,
            ContentVersion.tenant_id == job.tenant_id,
        )
        .order_by(ContentVersion.version_number.desc())
        .first()
    )
    latest_number = getattr(latest_version, "version_number", 0) if latest_version else 0
    return max(0, int(latest_number or 0)) + 1


def validate_publish_transition(attempt: PublishAttempt, action: str) -> str:
    """Validate action for a PublishAttempt and return new status."""
    allowed_from = PUBLISH_ATTEMPT_TRANSITIONS.get(action)
    if allowed_from is None:
        raise ValueError(f"Unknown publish action '{action}'")

    if attempt.status not in allowed_from:
        raise ValueError(
            f"Cannot '{action}' a PublishAttempt in status '{attempt.status}'. "
            f"Allowed from: {allowed_from}"
        )
    status_map = {
        "queue": "queued",
        "publishing": "publishing",
        "success": "success",
        "failed": "failed",
        "retrying": "retrying",
    }
    return status_map[action]


def transition_publish_attempt(db: Session, attempt_id: int, action: str) -> Optional[PublishAttempt]:
    """Execute a state transition on a PublishAttempt."""
    attempt = db.query(PublishAttempt).filter(PublishAttempt.id == attempt_id).first()
    if not attempt:
        return None

    new_status = validate_publish_transition(attempt, action)
    attempt.status = new_status
    db.commit()
    db.refresh(attempt)
    logger.info("PublishAttempt %d transitioned: %s -> %s", attempt_id, action, new_status)
    return attempt


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
        "partial_success": "partially_published",
        "complete": "published",
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

    Priority order:
    1. article_slots from generation_config (individual slot configs)
    2. article_count from generation_config (simple count)
    3. Default: 1 slot
    """
    config = job.generation_config or {}
    slots_data = config.get("article_slots")
    public_count = config.get("public_count", 0)
    private_count = config.get("private_count", 0)

    slots = []

    if slots_data:
        # Use article_slots configuration (from scheduled tasks / publish plans)
        for i, slot_cfg in enumerate(slots_data):
            content_type = (
                slot_cfg.get("content_type", "image_text")
                if isinstance(slot_cfg, dict)
                else "image_text"
            )
            publish_domain = (
                slot_cfg.get("publish_domain", "public")
                if isinstance(slot_cfg, dict)
                else "public"
            )
            slot = ContentJobArticle(
                tenant_id=job.tenant_id,
                job_id=job.id,
                content_type=content_type,
                sort_order=i,
                publish_domain=publish_domain,
                status="pending",
            )
            db.add(slot)
            slots.append(slot)
    else:
        # Fallback: create based on article_count
        count = config.get("article_count", 1)
        remaining_public = max(public_count, count) if public_count else count
        remaining_private = private_count or 0

        for i in range(count):
            if remaining_private > 0:
                domain = "private"
                remaining_private -= 1
            elif remaining_public > 0:
                domain = "public"
                remaining_public -= 1
            else:
                domain = "public"

            slot = ContentJobArticle(
                tenant_id=job.tenant_id,
                job_id=job.id,
                content_type=job.content_type or "article",
                sort_order=i,
                publish_domain=domain,
                status="pending",
            )
            db.add(slot)
            slots.append(slot)

    db.commit()
    for s in slots:
        db.refresh(s)
    logger.info("Created %d slot articles for job %d", len(slots), job.id)
    return slots


def remove_unavailable_image_slots(raw_content: str, *, job_id: int, slot_index: int) -> str:
    """移除图片生成失败后未被填充的图片槽位。

    定时和批量任务的正文由内容 Agent 先写入 ``[IMAGE:...]`` 槽位，再交给
    图片生成服务替换。历史实现会在替换失败后注入 Picsum 随机图片，导致用户把
    第三方随机图误认为 AI 仿写结果，且后续归档会掩盖其真实来源。

    这里明确把“没有生成结果”表示为“没有图片”：保留正文，删除所有未填充槽位，
    并规范空行。调用方仍可继续产出待审核内容，运维则可通过任务和文章槽位定位
    万相或上游 Agent 的实际失败原因。
    """
    if not raw_content:
        logger.error(
            "任务图片生成失败，正文为空且没有可清理的图片槽位 job=%s slot=%s",
            job_id,
            slot_index,
        )
        return raw_content

    image_slot_count = len(re.findall(r"\[IMAGE:[^\]]*\]", raw_content))
    cleaned_content = re.sub(r"\[IMAGE:[^\]]*\]", "", raw_content)
    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()
    logger.error(
        "任务图片生成失败，已移除未填充图片槽位 job=%s slot=%s image_slots=%s；"
        "随机图库回退已阻止，请检查万相诊断日志",
        job_id,
        slot_index,
        image_slot_count,
    )
    return cleaned_content


def record_tageai_generation_progress(
    db: Session,
    job: ContentJob,
    *,
    stage: str,
    text_progress: int,
    media_total: int = 0,
    media_ready: int = 0,
    media_generating: int = 0,
    media_failed: int = 0,
) -> None:
    """将长耗时生成阶段写入可恢复的公开进度快照。

    ``generation_config`` 中的其他字段是本次任务冻结的输入配置，不能原地修改后依赖
    ORM 是否侦测 JSON 深层变化；这里总是创建新字典再赋值。每次提交同时更新心跳，
    Gateway 即使漏掉回调，也能从同一持久化事实恢复平台总进度。
    """

    def bounded(value: int) -> int:
        return max(0, min(100, int(value)))

    config = dict(getattr(job, "generation_config", None) or {})
    config["progress_snapshot"] = {
        "platform": "wechat",
        "stage": str(stage).strip().upper(),
        "text_progress": bounded(text_progress),
        "media_total": bounded(media_total),
        "media_ready": bounded(media_ready),
        "media_generating": bounded(media_generating),
        "media_failed": bounded(media_failed),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    job.generation_config = config
    db.commit()


def resolve_image_generation_limit(config: object) -> int:
    """读取队列实际消费的图片预算，并保留用户显式指定的零值。

    Gateway 在创建任务时已经完成预算校验，Worker 这里只负责安全地消费冻结快照。历史上
    使用 ``max(1, ...)`` 把 ``image_count=0`` 改成了一张图片，随后图片分析又可能扩展
    成多个槽位，既违背用户要求也让长任务一直停在媒体阶段。缺失或损坏的旧配置继续使用
    五张的兼容默认值；合法的 0 到 8 则原样保留，绝不把 0 当作 falsy 缺省值。
    """

    source = config if isinstance(config, dict) else {}
    generation_budget = source.get("generation_budget")
    budget = generation_budget if isinstance(generation_budget, dict) else {}
    configured_limit = budget.get("image_count", 5)
    if isinstance(configured_limit, bool) or not isinstance(configured_limit, int):
        return 5
    return max(0, min(configured_limit, 8))


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

        configured_image_limit = resolve_image_generation_limit(config)
        state = ArticleState(
            task_id=f"job_{job.id}_{slot_index}",
            user_id=job.created_by or 0,
            tenant_id=job.tenant_id,
            topic=topic,
            style=config.get("style", "default"),
            footer_template=job.footer_template,
            max_generated_images=max(1, min(configured_image_limit, 8)),
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
                        # 版式不能混合多篇文章，固定使用排序第一篇的原始 HTML。
                        state.reference_html = articles[0].body_html or None
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

        # TaGeAI 的参数必须在进入第一个 Agent 前转换为 ArticleState。这样仿写参考、
        # 指定标题和内容约束都会被既有提示词链真实消费，而不是只写在 generation_config。
        state = await apply_tageai_generation_context(state, config)
        raise_if_job_cancellation_requested(db, job.id)

        # Step 1: Title
        state = await agent1_generate_title_options(state)
        raise_if_job_cancellation_requested(db, job.id)
        if not state.title_options:
            raise Exception("Title generation failed")
        first = state.title_options[0]
        state.title = SelectedTitle(main_title=first.main_title, sub_title=first.sub_title)

        # Step 2: Outline
        state = await agent2_generate_outline(state)
        raise_if_job_cancellation_requested(db, job.id)

        # Step 3: Content — extract image keywords BEFORE merge to use for cleanup
        state = await agent3_generate_content(state)
        raise_if_job_cancellation_requested(db, job.id)
        raw_content = state.content or ""
        image_keywords_from_content = re.findall(r'keywords=([^,\]]+)', raw_content)
        # 正文已经真实生成，但媒体槽位尚未分析完成；先提交 TEXT_READY，让 Gateway
        # 能区别“正文已就绪、素材仍在准备”与“模型仍在写正文”。
        record_tageai_generation_progress(db, job, stage="TEXT_READY", text_progress=100)

        # Step 4: Images. 零预算是纯文字任务，必须跳过图片需求分析、图片生成和后续
        # 封面生成；否则即使正文不插图，封面模型仍会造成不符合用户指令的长耗时调用。
        if configured_image_limit == 0:
            record_tageai_generation_progress(
                db, job, stage="TEXT_READY", text_progress=100,
                media_total=0, media_ready=0, media_generating=0, media_failed=0,
            )
        # 非零预算下才进入既有图片链路。失败时保留正文并清理图片槽位，绝不能用随机图伪装成功。
        else:
            try:
                state = await agent4_analyze_image_requirements(state)
                media_total = len(state.image_requirements or [])
                record_tageai_generation_progress(
                    db, job, stage="MEDIA_GENERATING", text_progress=100,
                    media_total=media_total, media_generating=media_total,
                )
                completed_media = 0

                def on_image_progress(_message: str) -> None:
                    """每张图片返回后刷新心跳，避免长媒体阶段被桌面端误判为失活。"""

                    nonlocal completed_media
                    completed_media += 1
                    record_tageai_generation_progress(
                        db, job, stage="MEDIA_GENERATING", text_progress=100,
                        media_total=media_total, media_ready=min(completed_media, media_total),
                        media_generating=max(0, media_total - completed_media),
                    )

                state = await agent5_generate_images(state, stream_handler=on_image_progress)
                raise_if_job_cancellation_requested(db, job.id)
                media_ready = len([image for image in state.images if image.url])
                record_tageai_generation_progress(
                    db, job, stage="MEDIA_GENERATING", text_progress=100,
                    media_total=media_total, media_ready=media_ready,
                    media_failed=max(0, media_total - media_ready),
                )
            except Exception as img_exc:
                logger.exception(
                    "任务图片生成异常 job=%s slot=%s error_type=%s error=%s",
                    job.id,
                    slot_index,
                    type(img_exc).__name__,
                    str(img_exc)[:500],
                )

        # 没有生成图片时，正文仍可进入审核，但必须显式移除未填充槽位。
        if not state.images:
            state.full_content = remove_unavailable_image_slots(
                raw_content,
                job_id=job.id,
                slot_index=slot_index,
            )
            # Append footer if configured
            if job.footer_template:
                footer = job.footer_template.strip()
                if footer:
                    state.full_content = f"{state.full_content}\n\n---\n\n{footer}"
        else:
            # Apply watermark before merging if configured
            if state.images:
                try:
                    from app.services.asset_archive_service import save_image_to_asset_library
                    from app.services.storage_service import storage_service as _ss

                    wm_enabled = None
                    if job.generation_config:
                        wm_enabled = job.generation_config.get("watermark_enabled")

                    for img in state.images:
                        if not img.url:
                            continue
                        asset = await save_image_to_asset_library(
                            db, job.tenant_id, img.url,
                            keywords=img.keywords or "",
                            watermark_enabled=wm_enabled,
                        )
                        if asset:
                            img.url = _ss.get_url(asset.storage_key)
                except Exception as wm_exc:
                    logger.warning("Watermark failed for job %d slot %d: %s", job.id, slot_index, wm_exc)

            state = merge_images_into_content(state)
            from app.services.article_publication_polish_service import append_ai_image_disclaimer

            state.full_content = append_ai_image_disclaimer(state.full_content or state.content or "")

        # Step 5: Generate AI cover image (and watermark it)
        raise_if_job_cancellation_requested(db, job.id)
        cover_url = next(
            (img.url for img in state.images if getattr(img, "position", None) == 1),
            None,
        )
        from app.services.image_generation_service import is_image_generation_configured

        # 纯文字任务不创建 AI 封面。封面同样属于图片生成预算，不能因它不参与正文合并
        # 就绕过 image_count=0 的明确约束。
        if configured_image_limit > 0 and not cover_url and is_image_generation_configured():
            try:
                from app.services.image_generation_service import image_generation_service
                main_title_text = first.main_title
                cover_prompt = f"公众号文章封面图：{main_title_text}。扁平化设计，简洁大气，适合社交媒体传播。"
                ai_cover = await image_generation_service.generate_image(
                    cover_prompt,
                    size="1024*1024",
                    tenant_id=job.tenant_id,
                )
                if ai_cover:
                    cover_url = ai_cover
            except Exception as cover_err:
                logger.warning("Cover image generation failed for job %d slot %d: %s", job.id, slot_index, cover_err)

        # Apply watermark to cover image if generated separately
        if cover_url:
            try:
                from app.services.asset_archive_service import save_image_to_asset_library
                from app.services.storage_service import storage_service as _ss

                wm_enabled = None
                if job.generation_config:
                    wm_enabled = job.generation_config.get("watermark_enabled")

                asset = await save_image_to_asset_library(
                    db, job.tenant_id, cover_url,
                    keywords="cover",
                    watermark_enabled=wm_enabled,
                )
                if asset:
                    cover_url = _ss.get_url(asset.storage_key)
            except Exception as wm_exc:
                logger.warning("Cover watermark failed for job %d slot %d: %s", job.id, slot_index, wm_exc)

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
    raise_if_job_cancellation_requested(db, job.id)
    job.status = "generating"
    record_tageai_generation_progress(db, job, stage="TEXT_GENERATING", text_progress=0)

    # Load slots with their content_type
    slots = (
        db.query(ContentJobArticle)
        .filter(
            ContentJobArticle.job_id == job.id,
            ContentJobArticle.tenant_id == job.tenant_id,
        )
        .order_by(ContentJobArticle.sort_order)
        .all()
    )
    first_version_number = next_content_version_number(db, job)

    for i in range(count):
        # 每个文章槽位开始前读取一次最新状态，避免前一个槽位完成后继续生成下一个。
        raise_if_job_cancellation_requested(db, job.id)
        slot = slots[i] if i < len(slots) else None
        content_type = slot.content_type if slot else "image_text"

        try:
            if content_type == "image_text":
                # Use standard 5-agent pipeline for image_text
                result = asyncio.run(_run_slot(i))
                body_md = result["body_markdown"]
                cover = result.get("cover_url")
                images = result.get("images", [])
            else:
                # Use adapter for video / pure_image
                from app.services.content_adapters import get_generator
                gen = get_generator(content_type)
                gen_result = gen.generate(db, job, slot)

                body_md = gen_result.get("body_markdown", "")
                cover = gen_result.get("cover_url")
                images = gen_result.get("images", [])
                result = gen_result  # for the asset block below

            version = ContentVersion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                version_number=first_version_number + i,
                title=result.get("title", topic)[:255],
                body_markdown=body_md,
                summary=result.get("summary", topic)[:200],
                article_content_type=content_type,
                source="agent",
                created_by=job.created_by,
                model_metadata={"cover_url": cover} if cover else None,
            )
            db.add(version)
            db.flush()

            # Save images to asset library
            if images:
                image_urls = [img["url"] if isinstance(img, dict) else img for img in images]
                try:
                    from app.services.asset_archive_service import save_images_to_asset_library
                    asyncio.run(save_images_to_asset_library(
                        db, job.tenant_id, image_urls,
                        watermark_enabled=False,
                    ))
                except Exception as arch_exc:
                    logger.warning("Slot %d asset archive failed: %s", i, arch_exc)

            versions.append(version)
            logger.info("Job %d slot %d generated: %s", job.id, i, result["title"][:60])

        except TageAiGenerationContextError:
            # 仿写参考无法解析时不能生成空版本后继续自动审批；向上层传播以把整个
            # ContentJob 收敛为失败，防止任务退化成普通生成。
            raise
        except Exception as exc:
            logger.error("Slot %d processing failed for job %d: %s", i, job.id, exc)
            version = ContentVersion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                version_number=first_version_number + i,
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

        # ALWAYS preserve markdown image lines (![alt](url)) AND HTML img tags
        if re.match(r'^!\[.*\]\(.*\)$', s) or re.match(r'^<img\s+[^>]+/?>$', s, re.IGNORECASE):
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
