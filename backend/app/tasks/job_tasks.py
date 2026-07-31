"""Celery tasks for content job queue processing and asset cleanup."""

import logging
import re
import uuid

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ContentJob, ContentVersion, Article, PublishAttempt
from app.services.job_queue_service import process_job_batch, transition_job

logger = logging.getLogger(__name__)


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

        if job.status != "queued":
            return {"error": f"Job {job_id} is not in queued state (status={job.status})"}

        # 非文章类型转派到对应处理任务
        ct = job.content_type or "article"
        if ct in ("image", "pure_image"):
            from app.tasks.content_tasks import process_image_job
            db.close()
            return process_image_job.delay(job_id)
        elif ct == "video":
            from app.tasks.content_tasks import process_video_job
            db.close()
            return process_video_job.delay(job_id)

        # 文章类型：运行原有生成流水线
        versions = process_job_batch(db, job)

        # Create Article records from ContentVersions
        _save_versions_as_articles_and_drafts(db, job, versions)

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
    """Beat task: Look for queued jobs and dispatch them to the proper worker by content_type."""
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
            ct = job.content_type or "article"
            if ct in ("image", "pure_image"):
                from app.tasks.content_tasks import process_image_job
                process_image_job.delay(job.id)
            elif ct == "video":
                from app.tasks.content_tasks import process_video_job
                process_video_job.delay(job.id)
            else:
                process_content_job.delay(job.id)
            logger.info("Dispatched job %d (%s) to worker", job.id, ct)
        return {"dispatched": len(jobs)}
    finally:
        db.close()


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
            status="draft_saved",
            phase="DRAFT_SAVED",
        )
        db.add(article)
        db.flush()
        v.article_id = article.id

        # Create PublishAttempt for each account
        for aid in account_ids:
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
                    media_id = pub_result.get("media_id", "")
                    if media_id:
                        article.publish_id = str(media_id)
                        attempt.platform_media_id = str(media_id)
                    attempt.status = "success"
                elif publish_mode == "direct":
                    from app.services.wechat_publisher import publish_article
                    pub_result = publish_article(db, article, aid, mode="direct", tenant_id=job.tenant_id, actor_id=job.created_by or 0)
                    logger.info("Article %d published directly to account %s, publish_id=%s",
                                article.id, aid, pub_result.get("publish_id", ""))
                    article.status = "publishing"
                    article.phase = "PUBLISHING"
                    if pub_result.get("publish_id"):
                        article.publish_id = str(pub_result["publish_id"])
                    elif pub_result.get("media_id"):
                        article.publish_id = str(pub_result["media_id"])
                    attempt.status = "publishing"
                    attempt.platform_media_id = article.publish_id
                else:
                    from app.services.wechat_publisher import save_article_as_draft
                    draft_result = save_article_as_draft(db, article, aid, tenant_id=job.tenant_id, actor_id=job.created_by or 0)
                    media_id = draft_result.get("media_id", "")
                    logger.info("Article %d saved as WeChat draft via account %s, media_id=%s",
                                article.id, aid, media_id)
                    if media_id:
                        article.publish_id = str(media_id)
                        attempt.platform_media_id = str(media_id)
                    attempt.status = "success"
            except Exception as pub_err:
                logger.warning("Publish to account %s for article %d failed: %s", aid, article.id, pub_err)
                attempt.status = "failed"
                attempt.error_message = str(pub_err)[:500]
                article.error_message = str(pub_err)[:500]

        db.commit()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def poll_publishing_articles(self):
    """定时任务：轮询「发布中」状态的文章，发布完成后自动获取 msg_data_id"""
    db = MysqlSessionLocal()
    try:
        from app.services.wechat_gateway_policy import is_wechat_relay_enabled
        if is_wechat_relay_enabled():
            logger.info("Skip publish polling: relay mode requires relay freepublish/get endpoint")
            return {
                "polled": 0,
                "skipped": True,
                "reason": "relay mode requires relay freepublish/get endpoint",
            }

        from app.models.mysql_models import Article, AccountCredential, WeChatAccount

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
