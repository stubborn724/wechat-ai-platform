"""Celery tasks for content job queue processing and asset cleanup."""

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import Article, ContentJob, ContentVersion, PublishAttempt, TageAiPublishCandidate
from app.services.job_queue_service import (
    JobCancellationRequested,
    claim_dispatched_job_for_execution,
    claim_queued_job_for_dispatch,
    process_job_batch,
    raise_if_job_cancellation_requested,
    recover_stale_dispatch_claims,
    release_dispatch_claim,
    transition_job,
)

logger = logging.getLogger(__name__)


def _create_wechat_relay_client():
    """创建专用于最终状态读取的中转站客户端。

    公众号 AppSecret 只在初次发布时交给中转站；后续状态查询仅携带 HMAC 和
    ``publish_id``。单独保留工厂函数使 Celery 单测可以注入替身，避免测试访问真实
    固定 IP 服务，也避免把连接配置散落在轮询循环中。
    """

    from app.config import settings
    from app.services.wechat_relay_client import WeChatRelayClient

    return WeChatRelayClient(
        base_url=settings.wechat_relay_base_url,
        relay_app_id=settings.wechat_relay_app_id,
        relay_secret=settings.wechat_relay_secret,
    )


class ContentGenerationFailed(RuntimeError):
    """内容任务没有可投递正文时的受控失败。

    该异常与模型网络异常不同：版本记录可能已经写入审计库，但正文为空时绝不能继续创建
    草稿或自动批准任务。调用方需要将 ContentJob 收敛为明确失败，而不是让 Celery 重试时
    产生重复投递副作用。
    """


def require_deliverable_versions(versions):
    """验证本次生成的全部版本都具备可保存的正文。

    生成流水线会保留失败版本用于审计。投递边界不能把这类空正文静默跳过后仍将整个任务
    视为成功，否则 Gateway 会长期等待草稿终态。批量任务任一版本为空时统一失败，要求
    调用方显式重试或人工处理，避免只投递部分文章却报告整批已完成。
    """

    invalid_versions = [
        version for version in versions
        if not isinstance(getattr(version, "body_markdown", None), str)
        or not version.body_markdown.strip()
    ]
    if not versions or invalid_versions:
        raise ContentGenerationFailed("文章生成失败，未得到可保存的正文")
    return versions


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_content_job(self, job_id: int):
    """Process a queued content job through the generation pipeline.

    Only handles 'article' type jobs. Image/video jobs are handled by
    process_image_job / process_video_job in content_tasks.py.
    """
    db = MysqlSessionLocal()
    try:
        job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        if job.status in {"cancel_requested", "cancelled"}:
            # 已排队但尚未取走的 Celery 消息仍可能启动。此时把取消请求确认成终态并返回
            # 正常结果，而不是进入异常重试或把任务重新送回生成链路。
            if job.status == "cancel_requested":
                job.status = "cancelled"
                db.commit()
            return {"job_id": job_id, "status": "cancelled"}

        # 兼容历史调用方误把图片/视频 ID 投到文章 Worker。必须在领取前转发，让实际的
        # 图片/视频 Worker 自己执行条件领取；否则这里先改成 generating 会使下游 Worker
        # 误判任务已被执行而直接退出。
        ct = job.content_type or "article"
        if ct in ("image", "pure_image"):
            from app.tasks.content_tasks import process_image_job
            db.close()
            return process_image_job.delay(job_id)
        if ct == "video":
            from app.tasks.content_tasks import process_video_job
            db.close()
            return process_video_job.delay(job_id)

        # Broker 重投、Worker 重启和历史入口都可能产生重复消息。只有成功执行条件更新的
        # Worker 可以进入生成流程，其余副本读取当前事实后结束，绝不能再次调用模型或投递公众号。
        job = claim_dispatched_job_for_execution(db, job_id)
        if not job:
            current = db.query(ContentJob).filter(ContentJob.id == job_id).first()
            return {"job_id": job_id, "status": current.status if current else "missing", "ignored": True}

        if job.content_type == "article_publish_existing":
            # 正式发布不能再次进入模型生成流水线。候选在平台事务中已被唯一占用，这里
            # 只读取它冻结的 ContentVersion/Article，并把真实微信投递结果写回同一篇文章。
            return _publish_preview_candidate_article(db, job)

        # 文章类型：运行原有生成流水线
        versions = require_deliverable_versions(process_job_batch(db, job))

        # 生成已经返回，但投递会触发真实草稿或发布副作用；取消优先在这里停止，不能
        # 让已生成的正文穿过到发布器。
        raise_if_job_cancellation_requested(db, job_id)

        # Create Article records from ContentVersions
        _save_versions_as_articles_and_drafts(db, job, versions)

        raise_if_job_cancellation_requested(db, job_id)

        # Determine post-generation flow
        if job.approval_mode == "auto":
            # Auto mode: approve and publish directly
            transition_job(db, job_id, "approve")
            logger.info("Job %d auto-approved after generation", job_id)
        else:
            # Manual mode: wait for human review
            job.status = "awaiting_review"
            db.commit()
            logger.info("Job %d completed, awaiting review", job_id)

        return {
            "job_id": job_id,
            "versions_created": len(versions),
            "status": job.status,
        }

    except ContentGenerationFailed as exc:
        # ``process_job_batch`` 已可能提交失败版本作为审计记录；这里不能再抛给 Celery
        # 自动重试，否则同一草稿请求会在缺少正文的情况下反复创建副作用。将任务标为
        # FAILED 后由外部 Invocation 如实投影错误，等待用户显式决定后续操作。
        db.rollback()
        current_job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if current_job:
            current_job.status = "failed"
            current_job.error_code = "CONTENT_GENERATION_FAILED"
            current_job.error_message = str(exc)[:500]
            db.commit()
        logger.error("Job %d stopped before delivery: %s", job_id, exc)
        return {
            "job_id": job_id,
            "status": "failed",
            "error_code": "CONTENT_GENERATION_FAILED",
        }
    except JobCancellationRequested:
        # 取消不是失败。先回滚当前 Worker 尚未提交的版本、文章和投递尝试，再读取
        # 持久化任务把 cancel_requested 确认成 cancelled，确保任何后续查询都看到终态。
        db.rollback()
        current_job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if current_job and current_job.status in {"cancel_requested", "cancelled"}:
            current_job.status = "cancelled"
            db.commit()
        logger.info("Job %d stopped after cancellation request", job_id)
        return {"job_id": job_id, "status": "cancelled"}
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
    """Beat 以原子领取方式补投队列任务，避免多个 Beat 重复派发同一任务。"""
    db = MysqlSessionLocal()
    try:
        recovered = recover_stale_dispatch_claims(db)
        jobs = (
            db.query(ContentJob)
            .filter(ContentJob.status == "queued")
            .order_by(ContentJob.created_at.asc())
            .limit(5)
            .all()
        )
        dispatched = 0
        for job in jobs:
            # ORM 查询只能得到候选，不能作为派发凭据。条件更新成功才代表当前 Beat 获得了
            # 领取权；并发 Beat 会在这里自然分叉，避免向 Celery 发送重复消息。
            claimed = claim_queued_job_for_dispatch(db, job.id)
            if not claimed:
                continue
            ct = job.content_type or "article"
            try:
                if ct in ("image", "pure_image"):
                    from app.tasks.content_tasks import process_image_job
                    process_image_job.delay(job.id)
                elif ct == "video":
                    from app.tasks.content_tasks import process_video_job
                    process_video_job.delay(job.id)
                else:
                    process_content_job.delay(job.id)
                dispatched += 1
                logger.info("Dispatched claimed job %d (%s) to worker", job.id, ct)
            except Exception:
                # Broker 发布失败时释放仍未被 Worker 领取的状态；若消息其实已经抵达且 Worker
                # 已切到 generating，条件释放会返回 False 而不会覆盖真实执行状态。
                release_dispatch_claim(db, job.id)
                logger.exception("Failed to dispatch claimed job %d (%s)", job.id, ct)
        return {"dispatched": dispatched, "recovered": recovered}
    finally:
        db.close()


@celery_app.task
def deliver_tageai_callback_outbox():
    """根据真实任务状态生成并投递 TaGeAI 回调，失败事件由 outbox 有限重试。"""

    from app.integrations.tageai.callback_delivery import deliver_due_callback_events
    from app.integrations.tageai.service import enqueue_current_callback_snapshots

    snapshots_created = enqueue_current_callback_snapshots()
    delivery = deliver_due_callback_events()
    return {"snapshots_created": snapshots_created, **delivery}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def sync_comments_for_published_articles(self):
    """定时任务：按租户分组，对所有已发布且有 msg_data_id 的文章同步评论"""
    db = MysqlSessionLocal()
    try:
        from collections import defaultdict
        from app.models.mysql_models import Article, WeChatComment, WeChatAccount
        from app.services.wechat_oauth_service import get_valid_token_sync

        articles = (
            db.query(Article)
            .filter(
                Article.msg_data_id.isnot(None),
                Article.msg_data_id != "",
                Article.status == "published",
            )
            .order_by(Article.id.desc())
            .all()
        )

        if not articles:
            return {"synced": 0, "articles": 0}

        # 按租户分组文章
        articles_by_tenant = defaultdict(list)
        for article in articles:
            tid = article.tenant_id or 0
            articles_by_tenant[tid].append(article)

        from app.services.wechat_comment_service import sync_comments_with_auto as _sync_comments

        total_synced = 0
        total_articles = 0

        for tenant_id, tenant_articles in articles_by_tenant.items():
            # 取该租户下的第一个活跃公众号
            account = (
                db.query(WeChatAccount)
                .filter(
                    WeChatAccount.tenant_id == tenant_id,
                    WeChatAccount.deleted_at.is_(None),
                )
                .first()
            )
            if not account:
                logger.warning("No WeChat account found for tenant %d, skipping %d articles",
                               tenant_id, len(tenant_articles))
                continue

            for article in tenant_articles:
                try:
                    new_count, _ = _sync_comments(
                        db, tenant_id, account.id, article.msg_data_id,
                    )
                    if new_count > 0:
                        db.query(WeChatComment).filter(
                            WeChatComment.msg_id == article.msg_data_id,
                            WeChatComment.article_id.is_(None),
                        ).update({"article_id": article.id})
                        db.commit()
                        total_synced += new_count
                except Exception as exc:
                    logger.warning("Sync comments for article %d (tenant %d) failed: %s",
                                   article.id, tenant_id, exc)
                total_articles += 1

        logger.info("Auto-sync comments: %d new comments from %d articles (%d tenants)",
                    total_synced, total_articles, len(articles_by_tenant))
        return {"synced": total_synced, "articles": total_articles}
    except Exception as exc:
        logger.error("sync_comments_for_published_articles failed: %s", exc)
    finally:
        db.close()


# 兼容 beat 调度（通过 import 暴露给 celerybeat 使用）
sync_comments_task = sync_comments_for_published_articles


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


def _save_versions_as_articles_and_drafts(db, job: ContentJob, versions):
    """Create Article records from ContentVersions and create PublishAttempts.

    For each version and each configured account, creates a PublishAttempt record
    that tracks the publish state per account independently.
    """
    config = job.generation_config or {}
    account_ids = config.get("account_ids", [job.account_id] if job.account_id else [])
    publish_mode = config.get("publish_mode", "draft")

    for v in versions:
        # 投递前必须读取最新取消标志。文章对象即使已经创建，也不能继续调用草稿箱或
        # 直接发布 API；外层 Worker 会回滚本次未提交对象并确认取消终态。
        raise_if_job_cancellation_requested(db, job.id)
        if not v.body_markdown:
            logger.warning("ContentVersion %d has no body, skipping article creation", v.id)
            continue

        cover_url = None
        if v.model_metadata and isinstance(v.model_metadata, dict):
            cover_url = v.model_metadata.get("cover_url")

        body = v.body_markdown or ""
        image_keywords = re.findall(r'keywords=([^,\]]+)', body)
        image_keywords.extend(re.findall(r'!\[([^\]]+)\]\([^)]+\)', body))
        body = re.sub(r'\[IMAGE:[^\]]*\]', '', body)
        photo_kw = ['俯拍', '仰拍', '侧拍', '微距', '特写', '近景', '远景', '中景',
                    '暖光', '逆光', '侧光', '顶光', '底光', '打光', '布光',
                    '景深', '光圈', '快门', '45度']
        cleaned_lines = []
        for line in body.split("\n"):
            s = line.strip()
            if not s:
                cleaned_lines.append(line)
                continue
            if re.match(r'^!\[.*\]\(.*\)$', s) or re.match(r'^<img\s+[^>]+/?>$', s, re.IGNORECASE):
                cleaned_lines.append(line)
                continue
            if image_keywords:
                skip = False
                for kw in image_keywords:
                    if len(kw) >= 6 and kw in s:
                        skip = True
                        break
                if skip:
                    continue
            if sum(1 for kw in photo_kw if kw in s) >= 2:
                continue
            if re.search(r'(?:右下角|左下角|右上角|左上角).*(?:水印|文字|标志|logo)', s, re.IGNORECASE):
                continue
            cleaned_lines.append(line)
        body = "\n".join(cleaned_lines)

        article = Article(
            task_id=f"job_{job.id}_v{v.id}_{uuid.uuid4().hex[:8]}",
            tenant_id=job.tenant_id,
            user_id=job.created_by,
            topic=v.title or job.topic,
            style=config.get("style", "default"),
            main_title=v.title,
            sub_title=(v.summary or "")[:200],
            content=body,
            full_content=body,
            cover_image=cover_url,
            # 正文已经生成不等于已投递；只有发布器返回可验证结果后才能进入草稿或
            # 发布状态，避免异常分支把未保存文章伪装成草稿成功。
            status="generated",
            phase="CONTENT_GENERATED",
        )
        db.add(article)
        db.flush()
        v.article_id = article.id

        if publish_mode == "preview":
            # 预览只负责把可读文章和不可变版本落库。不能创建 PublishAttempt，也不能调用
            # 草稿或正式发布 API，否则用户看到预览前就已经产生了公众号侧副作用。
            continue

        # Create PublishAttempt for each account
        for aid in account_ids:
            # 多账号投递时每个账号都重新检查，避免第一个投递期间产生的取消请求继续
            # 落到后续公众号账号。
            raise_if_job_cancellation_requested(db, job.id)
            attempt = PublishAttempt(
                tenant_id=job.tenant_id,
                job_id=job.id,
                account_id=aid,
                idempotency_key=f"publish_{job.id}_{aid}_{v.id}",
                mode=publish_mode,
                status="pending",
            )
            db.add(attempt)
            db.flush()

            try:
                content_type = v.article_content_type or "image_text"

                if content_type in ("video", "pure_image"):
                    # Use content adapters for non-image_text types
                    from app.services.content_adapters import get_publisher
                    adapter = get_publisher(content_type)
                    article_data = {
                        "id": article.id,
                        "title": v.title or article.topic,
                        "summary": v.summary or "",
                        "body_markdown": article.content or "",
                        "image_urls": json.loads(article.images) if isinstance(article.images, str) else (article.images or []),
                        "video_url": "",
                        "publish_mode": publish_mode,
                    }
                    pub_result = adapter.publish(db, article_data, aid, tenant_id=job.tenant_id)
                elif publish_mode == "direct":
                    from app.services.wechat_publisher import publish_article
                    pub_result = publish_article(db, article, aid, mode="direct", tenant_id=job.tenant_id, actor_id=job.created_by or 0)
                else:
                    from app.services.wechat_publisher import save_article_as_draft
                    draft_result = save_article_as_draft(db, article, aid, tenant_id=job.tenant_id, actor_id=job.created_by or 0)
                    pub_result = draft_result

                from app.services.publish_delivery_state_service import (
                    apply_publish_delivery_outcome,
                    resolve_publish_delivery_outcome,
                )

                outcome = resolve_publish_delivery_outcome(publish_mode, pub_result)
                apply_publish_delivery_outcome(article, attempt, outcome)
                logger.info(
                    "Article %d delivery finished for account %s: article_status=%s phase=%s",
                    article.id,
                    aid,
                    outcome.article_status,
                    outcome.article_phase,
                )
            except Exception as pub_err:
                logger.warning("Publish to account %s for article %d failed: %s", aid, article.id, pub_err)
                from app.services.publish_delivery_state_service import (
                    apply_publish_delivery_outcome,
                    failure_publish_delivery_outcome,
                )

                outcome = failure_publish_delivery_outcome(publish_mode, str(pub_err)[:500])
                apply_publish_delivery_outcome(article, attempt, outcome)

        db.commit()


def _publish_preview_candidate_article(db, job: ContentJob):
    """将已被用户确认的预览版本正式投递到其原始公众号。

    函数不接受正文、主题或账号列表，所有业务事实都来自创建发布任务时冻结的候选。这样
    即使桌面端或模型在确认后携带了不同参数，也无法改变实际发布的文章版本或目标账号。
    """

    config = job.generation_config or {}
    candidate_id = str(config.get("tageai_publish_candidate_id") or "").strip()
    if not candidate_id:
        raise ContentGenerationFailed("正式发布任务缺少预览候选")
    candidate = db.query(TageAiPublishCandidate).filter(
        TageAiPublishCandidate.candidate_id == candidate_id,
        TageAiPublishCandidate.tenant_id == job.tenant_id,
    ).first()
    if candidate is None or candidate.status not in {"RESERVED", "PUBLISHING"}:
        raise ContentGenerationFailed("发布候选已失效或未被确认")
    if candidate.account_id != job.account_id:
        raise ContentGenerationFailed("发布候选与任务公众号不匹配")

    version = db.query(ContentVersion).filter(
        ContentVersion.id == candidate.source_content_version_id,
        ContentVersion.tenant_id == job.tenant_id,
        ContentVersion.article_id == candidate.article_id,
    ).first()
    article = db.query(Article).filter(
        Article.id == candidate.article_id,
        Article.tenant_id == job.tenant_id,
    ).first()
    if version is None or article is None or not str(article.content or "").strip():
        raise ContentGenerationFailed("发布候选关联的预览文章不可用")

    attempt = PublishAttempt(
        tenant_id=job.tenant_id,
        job_id=job.id,
        account_id=job.account_id,
        idempotency_key=f"tageai-candidate-{candidate.id}",
        mode="direct",
        status="pending",
    )
    db.add(attempt)
    db.flush()
    candidate.status = "PUBLISHING"
    try:
        from app.services.publish_delivery_state_service import apply_publish_delivery_outcome, resolve_publish_delivery_outcome
        from app.services.wechat_publisher import publish_article

        result = publish_article(
            db,
            article,
            job.account_id,
            mode="direct",
            tenant_id=job.tenant_id,
            actor_id=job.created_by or 0,
        )
        outcome = resolve_publish_delivery_outcome("direct", result)
        apply_publish_delivery_outcome(article, attempt, outcome)
        candidate.status = "PUBLISHED" if outcome.article_status == "published" else "PUBLISHING"
        transition_job(db, job.id, "approve")
        db.commit()
        return {"job_id": job.id, "status": job.status, "article_id": article.id}
    except Exception as exc:
        from app.services.publish_delivery_state_service import apply_publish_delivery_outcome, failure_publish_delivery_outcome

        outcome = failure_publish_delivery_outcome("direct", str(exc)[:500])
        apply_publish_delivery_outcome(article, attempt, outcome)
        candidate.status = "FAILED"
        job.status = "failed"
        job.error_code = "PUBLISH_SUBMISSION_FAILED"
        job.error_message = str(exc)[:500]
        db.commit()
        return {"job_id": job.id, "status": "failed", "error_code": job.error_code}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def poll_publishing_articles(self):
    """定时任务：轮询「发布中」状态的文章，发布完成后自动获取 msg_data_id"""
    db = MysqlSessionLocal()
    try:
        from app.services.wechat_gateway_policy import is_wechat_relay_enabled
        if is_wechat_relay_enabled():
            from app.services.publish_delivery_state_service import apply_relay_publish_status

            relay_articles = (
                db.query(Article)
                .filter(
                    Article.status.in_(["publishing", "unknown"]),
                    Article.phase.in_(["RELAY_PUBLISHING", "PUBLISH_STATUS_UNKNOWN"]),
                )
                .all()
            )
            relay_client = _create_wechat_relay_client()
            status_counts = {"PUBLISHING": 0, "PUBLISHED": 0, "FAILED": 0, "UNKNOWN": 0}
            now = datetime.now(timezone.utc)
            for article in relay_articles:
                # Article 不直接保存 ContentJob 外键，必须经 ContentVersion 反查任务。
                # 不能使用 article.task_id：它是展示用的字符串而不是 content_jobs.id。
                version = (
                    db.query(ContentVersion)
                    .filter(
                        ContentVersion.tenant_id == article.tenant_id,
                        ContentVersion.article_id == article.id,
                    )
                    .order_by(ContentVersion.id.desc())
                    .first()
                )
                candidates = (
                    db.query(TageAiPublishCandidate)
                    .filter(
                        TageAiPublishCandidate.tenant_id == article.tenant_id,
                        TageAiPublishCandidate.article_id == article.id,
                    )
                    .all()
                )
                # 预览和正式发布分别创建 ContentJob：ContentVersion 只能反查到预览 Job，
                # 而真正的 PublishAttempt 归属 ``article_publish_existing`` Job。正式发布时
                # 写入的候选幂等键是两者唯一、稳定的关联事实；同时保留旧 Job 条件，兼容
                # 历史记录和非候选发布流程，避免轮询遗漏已受理的投递尝试。
                attempt_lookup_conditions = []
                if version:
                    attempt_lookup_conditions.append(PublishAttempt.job_id == version.job_id)
                candidate_attempt_keys = [
                    f"tageai-candidate-{candidate.id}"
                    for candidate in candidates
                    if getattr(candidate, "id", None) is not None
                ]
                if candidate_attempt_keys:
                    attempt_lookup_conditions.append(
                        PublishAttempt.idempotency_key.in_(candidate_attempt_keys)
                    )
                attempts = []
                if attempt_lookup_conditions:
                    attempts = (
                        db.query(PublishAttempt)
                        .filter(
                            PublishAttempt.tenant_id == article.tenant_id,
                            or_(*attempt_lookup_conditions),
                        )
                        .all()
                    )
                try:
                    relay_result = relay_client.query_publish_status(article.publish_id)
                except Exception as exc:
                    # 网络、部署滞后或微信暂不可达不能冒充明确失败；保留 UNKNOWN 并在
                    # 下一轮重新查询。异常摘要受长度限制，避免把响应或凭据写入日志。
                    relay_result = {
                        "relay_status": "UNKNOWN",
                        "message": f"中转站最终状态暂不可查：{str(exc)[:300]}",
                        "error_code": "PUBLISH_STATUS_UNKNOWN",
                    }
                final_status = apply_relay_publish_status(
                    article,
                    attempts,
                    candidates,
                    relay_result,
                    now=now,
                )
                status_counts[final_status] = status_counts.get(final_status, 0) + 1
            if relay_articles:
                db.commit()
                logger.info(
                    "Relay publish status polling finished: publishing=%d published=%d failed=%d unknown=%d",
                    status_counts["PUBLISHING"],
                    status_counts["PUBLISHED"],
                    status_counts["FAILED"],
                    status_counts["UNKNOWN"],
                )
            return {
                "polled": len(relay_articles),
                "publishing": status_counts["PUBLISHING"],
                "published": status_counts["PUBLISHED"],
                "failed": status_counts["FAILED"],
                "unknown_unresolved": status_counts["UNKNOWN"],
            }

        # Article 已在模块级导入；这里仅补充直连轮询专属的账号与凭据模型，避免
        # 局部导入遮蔽 relay 分支提前使用的 Article 名称。
        from app.models.mysql_models import AccountCredential, WeChatAccount

        articles = (
            db.query(Article)
            .filter(
                Article.status == "publishing",
                Article.publish_id.isnot(None),
                Article.publish_id != "",
            )
            .all()
        )

        if not articles:
            return {"polled": 0}

        import requests as _req
        from app.services.wechat_oauth_service import get_valid_token_sync

        completed = 0

        for article in articles:
            # 找到发布这篇文章所用的公众号（限定文章所属租户）
            tid = article.tenant_id or 0
            account = db.query(WeChatAccount).filter(
                WeChatAccount.tenant_id == tid,
                WeChatAccount.deleted_at.is_(None),
            ).first()

            if not account:
                logger.warning("No account for tenant %d in publish polling, skipping article %d",
                               tid, article.id)
                continue

            token = None
            try:
                token = get_valid_token_sync(db, account.id)
            except Exception:
                logger.warning("Failed to get token for account %d (tenant %d)", account.id, tid)
                continue

            if not token:
                logger.warning("No valid token for publish polling, article %d", article.id)
                continue

            try:
                # 查询 freepublish 状态
                resp = _req.post(
                    "https://api.weixin.qq.com/cgi-bin/freepublish/get",
                    params={"access_token": token},
                    json={"publish_id": article.publish_id},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("errcode", 0) != 0:
                    errcode = data.get("errcode")
                    # 710000 表示发布任务不存在或尚未开始处理，跳过
                    if errcode != 710000:
                        logger.warning("Publish poll error %d: %s", errcode, data.get("errmsg", ""))
                    continue

                pub_status = data.get("publish_status", -1)
                if pub_status == 0:  # 发布成功
                    # freepublish/get 返回 article_id，可以作为 msg_data_id
                    msg_data_id = str(data.get("article_id", ""))
                    article.msg_data_id = msg_data_id
                    article.status = "published"
                    article.phase = "PUBLISHED"

                    # 优先使用 article 自带的 tenant_id，退化到从 WeChatAccount 反查
                    tenant_id = article.tenant_id or 1

                    db.commit()

                    # 立即同步评论 + 自动回复 + 自动私信（新开 session 避免状态未提交）
                    try:
                        _sync_comments_after_publish(article.id, tenant_id, msg_data_id)
                    except Exception as sync_err:
                        logger.warning("Post-publish comment sync failed for article %d: %s", article.id, sync_err)

                    completed += 1
                elif pub_status in (1, 3):  # 发布失败
                    article.status = "failed"
                    article.phase = "PUBLISH_FAILED"
                    article.error_message = f"Publish failed (status={pub_status})"
                    db.commit()
                    logger.warning("Article %d publish failed (status=%d)", article.id, pub_status)
                # pub_status == 2: 仍在审核中，跳过
            except Exception as exc:
                logger.warning("Poll article %d failed: %s", article.id, exc)

        return {"polled": len(articles), "completed": completed}
    except Exception as exc:
        logger.error("poll_publishing_articles failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def _sync_comments_after_publish(article_id: int, tenant_id: int, msg_data_id: str):
    """发布完成后，同步评论并执行自动回复/私信"""
    import asyncio

    async def _do_sync():
        db_sync = MysqlSessionLocal()
        try:
            from app.models.mysql_models import Article as ArtModel, WeChatAccount as WxAcct
            article = db_sync.query(ArtModel).filter(ArtModel.id == article_id).first()
            if not article or not article.msg_data_id:
                return

            account = db_sync.query(WxAcct).filter(
                WxAcct.tenant_id == tenant_id,
                WxAcct.deleted_at.is_(None),
            ).first()
            if not account:
                logger.warning("No account for tenant %d in post-publish comment sync", tenant_id)
                return

            from app.services.wechat_comment_service import sync_comments_with_auto as _sync_auto
            result = await _sync_auto(db_sync, tenant_id, account.id, msg_data_id)

            # 关联 article_id
            if result.get("new", 0) > 0:
                from app.models.mysql_models import WeChatComment
                db_sync.query(WeChatComment).filter(
                    WeChatComment.msg_id == msg_data_id,
                    WeChatComment.article_id.is_(None),
                ).update({"article_id": article_id})
                db_sync.commit()

            logger.info(
                "Post-publish sync for article %d: %d new, auto_replied=%d, auto_msged=%d, skipped=%d",
                article_id, result["new"], result["auto_replied"],
                result["auto_messaged"], result["auto_skipped_msg"],
            )
        except Exception as exc:
            logger.error("Post-publish comment sync failed: %s", exc)
        finally:
            db_sync.close()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do_sync())
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def handle_wechat_message(self, message_id: int):
    """处理微信回调消息（关键词匹配+自动发送）"""
    from app.services.wechat_message_handler import process_incoming_message

    try:
        process_incoming_message(message_id)
    except Exception as exc:
        logger.error("Handle message %d failed: %s", message_id, exc)
        raise


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def execute_contact_delivery(self, delivery_id: int):
    """Celery 任务：异步执行资料发送

    注册: job_tasks.execute_contact_delivery
    调用链: API POST /leads/{id}/deliveries → create_delivery()
            → execute_contact_delivery.delay(delivery_id)

    异常需向外抛出，不能返回 error 字典被标记为成功
    """
    import asyncio
    from app.services.wechat_delivery_service import execute_delivery

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(execute_delivery(delivery_id))
    except Exception as exc:
        logger.error("Contact delivery %d failed: %s", delivery_id, exc)
        raise  # Celery 必须收到异常才能标记为失败
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def sync_comments_and_create_leads(self, job_id: int, tenant_id: int, account_id: int, scope: str = "all", article_id: int = None):
    """异步同步评论 + 自动创建线索（供评论线索工作台调用）"""
    import asyncio
    from app.services.wechat_lead_service import update_sync_job

    db = MysqlSessionLocal()
    try:
        update_sync_job(db, job_id, "running")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                _do_sync_and_create_leads(db, tenant_id, account_id, scope, article_id)
            )
        finally:
            loop.close()

        update_sync_job(db, job_id, "completed", result=result)
        logger.info("Sync job %d completed: %s", job_id, result)
        return result
    except Exception as exc:
        logger.error("Sync job %d failed: %s", job_id, exc)
        update_sync_job(db, job_id, "failed", error_message=str(exc))
        raise
    finally:
        db.close()


async def _do_sync_and_create_leads(db, tenant_id: int, account_id: int, scope: str, article_id: int = None):
    """执行同步并创建线索"""
    from app.models.mysql_models import Article, WeChatAccount
    from app.services.wechat_comment_service import _get_service

    svc = await _get_service(db, account_id)

    synced_articles = 0
    total_new_comments = 0
    total_new_leads = 0

    if scope == "article" and article_id:
        articles = db.query(Article).filter(
            Article.id == article_id,
            Article.msg_data_id.isnot(None),
            Article.msg_data_id != "",
        ).all()
    else:
        articles = db.query(Article).filter(
            Article.msg_data_id.isnot(None),
            Article.msg_data_id != "",
        ).all()

    for article in articles:
        try:
            new_ids, new_count, _ = await svc.sync_comments_to_db_v2(
                db, tenant_id, account_id, article.msg_data_id,
            )
            if new_count > 0:
                total_new_comments += new_count

                # 关联 article_id
                from app.models.mysql_models import WeChatComment
                db.query(WeChatComment).filter(
                    WeChatComment.msg_id == article.msg_data_id,
                    WeChatComment.article_id.is_(None),
                ).update({"article_id": article.id})
                db.commit()

                # 创建线索
                from app.services.wechat_lead_service import create_leads_from_comments
                created = create_leads_from_comments(db, tenant_id, account_id, new_ids)
                total_new_leads += created

            synced_articles += 1
        except Exception as exc:
            logger.warning("Sync article %d failed: %s", article.id, exc)
            continue

    return {
        "synced_articles": synced_articles,
        "new_comments": total_new_comments,
        "new_leads": total_new_leads,
    }
