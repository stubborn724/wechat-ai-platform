"""Celery tasks for content job queue processing and asset cleanup."""

import logging
import re
import uuid

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ContentJob, ContentVersion, Article
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

            # Create Article records from ContentVersions and save as WeChat drafts
            _save_versions_as_articles_and_drafts(db, job, versions)
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


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def sync_comments_for_published_articles(self):
    """定时任务：对所有已发布且有 msg_data_id 的文章同步评论"""
    db = MysqlSessionLocal()
    try:
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

        # 取第一个有凭据的公众号
        account = (
            db.query(WeChatAccount)
            .filter(WeChatAccount.deleted_at.is_(None))
            .first()
        )
        if not account:
            logger.warning("No WeChat account found for comment sync")
            return {"synced": 0, "articles": 0, "error": "no_account"}

        token = get_valid_token_sync(db, account.id)
        from app.services.wechat_comment_service import WeChatCommentService

        svc = WeChatCommentService(token)
        synced = 0
        for article in articles:
            try:
                new_count, _ = svc.sync_comments_to_db_sync(
                    db, article.user_id or 1, oauth.id, article.msg_data_id,
                )
                if new_count > 0:
                    db.query(WeChatComment).filter(
                        WeChatComment.msg_id == article.msg_data_id,
                        WeChatComment.article_id.is_(None),
                    ).update({"article_id": article.id})
                    db.commit()
                    synced += new_count
            except Exception as exc:
                logger.warning("Sync comments for article %d failed: %s", article.id, exc)

        logger.info("Auto-sync comments: %d new comments from %d articles", synced, len(articles))
        return {"synced": synced, "articles": len(articles)}
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
    """Create Article records from ContentVersions and save as WeChat drafts.

    For auto-approved jobs, this converts generated ContentVersion data into
    Article records and saves them to the WeChat draft box. Only saves as
    draft — does NOT submit for full publish (服务号 publishing).
    """
    for v in versions:
        if not v.body_markdown:
            logger.warning("ContentVersion %d has no body, skipping article creation", v.id)
            continue

        # Get cover URL from version model_metadata if available
        cover_url = None
        if v.model_metadata and isinstance(v.model_metadata, dict):
            cover_url = v.model_metadata.get("cover_url")

        # Final safety net: strip photography lines from body
        body = v.body_markdown or ""
        # Step 1: Extract image keywords from [IMAGE:] markers and markdown alt text
        image_keywords = re.findall(r'keywords=([^,\]]+)', body)
        image_keywords.extend(re.findall(r'!\[([^\]]+)\]\([^)]+\)', body))
        body = re.sub(r'\[IMAGE:[^\]]*\]', '', body)
        # Step 2: Remove lines matching image keywords or containing photography terms
        photo_kw = ['俯拍', '仰拍', '侧拍', '微距', '特写', '近景', '远景', '中景',
                    '暖光', '逆光', '侧光', '顶光', '底光', '打光', '布光',
                    '景深', '光圈', '快门', '45度']
        cleaned_lines = []
        for line in body.split("\n"):
            s = line.strip()
            if not s:
                cleaned_lines.append(line)
                continue
            # Always preserve markdown image lines AND HTML img tags
            if re.match(r'^!\[.*\]\(.*\)$', s) or re.match(r'^<img\s+[^>]+/?>$', s, re.IGNORECASE):
                cleaned_lines.append(line)
                continue
            # Remove if line matches any IMAGE keyword phrase
            if image_keywords:
                skip = False
                for kw in image_keywords:
                    if len(kw) >= 6 and kw in s:
                        skip = True
                        break
                if skip:
                    continue
            # Remove lines with 2+ photography terms
            if sum(1 for kw in photo_kw if kw in s) >= 2:
                continue
            if re.search(r'(?:右下角|左下角|右上角|左上角).*(?:水印|文字|标志|logo)', s, re.IGNORECASE):
                continue
            cleaned_lines.append(line)
        body = "\n".join(cleaned_lines)

        article = Article(
            task_id=f"job_{job.id}_v{v.id}_{uuid.uuid4().hex[:8]}",
            user_id=job.created_by,
            topic=v.title or job.topic,
            style=job.generation_config.get("style", "default") if job.generation_config else "default",
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

        # Link ContentVersion back to the Article
        v.article_id = article.id

        # Save to WeChat draft box if an account is configured
        if job.account_id:
            try:
                from app.services.wechat_publisher import save_article_as_draft

                draft_result = save_article_as_draft(db, article, job.account_id)
                media_id = draft_result.get("media_id", "")
                logger.info("Article %d saved as WeChat draft, media_id=%s", article.id, media_id)
                if media_id:
                    article.publish_id = str(media_id)
            except Exception as draft_err:
                logger.warning("Failed to save article %d as WeChat draft: %s", article.id, draft_err)
                article.error_message = str(draft_err)[:500]

        db.commit()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def poll_publishing_articles(self):
    """定时任务：轮询「发布中」状态的文章，发布完成后自动获取 msg_data_id"""
    db = MysqlSessionLocal()
    try:
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
            # 找到发布这篇文章所用的公众号
            publish_info = article.publish_id or ""
            # publish_id 格式可能是 "publish_id" 或 "media_id"
            # 从 published_at 等字段找不到 account_id，需要从关联数据推断
            # 退一步：尝试从 publish_id 匹配

            # 遍历所有有凭据的公众号尝试获取 token
            accounts = db.query(WeChatAccount).filter(
                WeChatAccount.deleted_at.is_(None),
            ).all()

            token = None
            for acct in accounts:
                try:
                    token = get_valid_token_sync(db, acct.id)
                    break
                except Exception:
                    continue

            if not token:
                logger.warning("No valid token for publish polling")
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

                    # 从 article 反查 tenant_id（WeChatAccount 有 tenant_id）
                    from app.models.mysql_models import WeChatAccount as WxAcct
                    acct = db.query(WxAcct).filter(
                        WxAcct.deleted_at.is_(None),
                    ).first()
                    tenant_id = acct.tenant_id if acct else 1

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
                WxAcct.deleted_at.is_(None),
            ).first()
            if not account:
                logger.warning("No account for post-publish comment sync")
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
