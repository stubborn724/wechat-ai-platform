"""纯图片与视频内容生成 Celery 任务"""

import asyncio
import json
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ContentAsset, ContentJob, ContentJobArticle, ContentVersion
from app.services.job_queue_service import claim_dispatched_job_for_execution
from app.services.storage_service import generate_object_key, storage_service

logger = logging.getLogger(__name__)


async def _process_image_job_sync(db: Session, job: ContentJob, req) -> dict:
    """Process an image job synchronously (inline, no Celery). Falls back to placeholder images on failure."""
    from app.services.image_generation_service import image_generation_service
    from app.services.storage_service import generate_object_key, storage_service
    from app.services.asset_archive_service import save_image_to_asset_library

    config = job.generation_config or {}
    topic = job.topic or ""
    size_map = {"1:1": "1024*1024", "3:4": "1024*1365", "9:16": "1024*1820", "16:9": "1820*1024", "4:3": "1365*1024"}
    img_size = size_map.get(config.get("aspect_ratio", "3:4"), "1024*1365")

    # Image count from feed source article (number of ![](http entries in reference)
    image_count = config.get("image_count", 0)
    feed_ids = (req.feed_article_ids or []) if hasattr(req, 'feed_article_ids') else []
    print(f"  [debug] image_count={image_count}, feed_ids={feed_ids}, hasattr={hasattr(req, 'feed_article_ids')}")
    if not image_count and feed_ids:
        from app.models.mysql_models import FeedSourceArticle
        ref = db.query(FeedSourceArticle).filter(FeedSourceArticle.id.in_(feed_ids)).first()
        if ref and ref.body_markdown:
            image_count = len([l for l in ref.body_markdown.split('\n\n') if l.strip().startswith("![](http")])
            print(f"  [debug] ref article has {image_count} images")
        else:
            print(f"  [debug] ref not found or no body_markdown")
    if image_count < 1:
        image_count = 3

    job.status = "generating"
    db.commit()

    image_urls = []
    image_keys = []
    assets = []

    styles = ["宽景构图，柔和自然光线，干净留白背景，高级质感",
              "细节特写，质感丰富，浅景深，柔和光影",
              "场景氛围，自然光线，干净构图，温暖色调",
              "俯视构图，精致布局，优雅空间感，柔和色调",
              "局部特写，材质纹理，细腻光影，艺术感",
              "全景展示，开阔视野，通透光线，现代感",
              "静谧氛围，柔和光线，留白构图，宁静致远",
              "动态瞬间，生动自然，真实场景，富有活力",
              "极简构图，几何美学，干净线条，高级感",
              "丰富层次，纵深感强，光影交错，沉浸氛围"]

    for i in range(image_count):
        style = styles[i % len(styles)]
        prompt = f"{topic}，{style}"
        print(f"  ▶ 图片 {i+1}/{image_count}")
        img_url = None
        try:
            img_url = await image_generation_service.generate_image(
                prompt,
                size=img_size,
                tenant_id=job.tenant_id,
            )
        except Exception as e:
            print(f"  ⚠️ 图片 {i+1} 生成失败: {e}")
        if not img_url:
            print(f"  ️ 图片 {i+1} 生成失败，跳过")
            continue
        try:
            asset = await save_image_to_asset_library(db, job.tenant_id, img_url, keywords=topic[:50], usage_type="generated_image")
            assets.append(asset)
            image_urls.append(img_url)
            image_keys.append(asset.storage_key)
        except Exception as e:
            print(f"  ⚠️ 图片 {i+1} 保存失败: {e}")
        ca = ContentAsset(tenant_id=job.tenant_id, job_id=job.id, asset_type="final_image",
                          storage_key=asset.storage_key if i < len(assets) else "",
                          file_format="jpg", sort_order=i, generation_config={"prompt": prompt, "seq": i})
        db.add(ca)
        db.commit()

    if not assets:
        raise RuntimeError("All image generations failed")

    # Detect gallery format from feed_article_ids
    is_gallery = False
    feed_ids = (req.feed_article_ids or []) if hasattr(req, 'feed_article_ids') else []
    if feed_ids:
        from app.models.mysql_models import FeedSourceArticle
        ref = db.query(FeedSourceArticle).filter(FeedSourceArticle.id.in_(feed_ids)).first()
        if ref and ref.body_markdown and ref.body_markdown.strip().startswith("![](http"):
            is_gallery = True

    # Build body_md with original source URLs (publicly accessible from WeChat CDN)
    body_md = "\n\n".join(f"![]({u})" for u in image_urls)

    if is_gallery and image_urls:
        thumbs = ""
        for i in range(len(image_urls)):
            img_url = storage_service.get_url(image_keys[i]) if i < len(image_keys) else image_urls[i]
            b = "#07c160" if i == 0 else "transparent"
            o = "1" if i == 0 else "0.6"
            thumbs += (
                f'<div style="flex:0 0 80px;height:60px;border-radius:6px;overflow:hidden;'
                f'cursor:pointer;border:2px solid {b};opacity:{o};transition:all .2s;" '
                f'onclick="let p=this.parentElement;'
                f'p.querySelectorAll(\'>div\').forEach(d=>{{d.style.border=\'2px solid transparent\';d.style.opacity=\'0.6\'}});'
                f'this.style.border=\'2px solid #07c160\';this.style.opacity=\'1\';'
                f'p.parentElement.querySelector(\'.gallery-main img\').src=\'{img_url}\';">'
                f'<img src="{img_url}" loading="lazy" '
                f'style="width:100%;height:100%;object-fit:cover;display:block;" />'
                f'</div>'
            )
        first_url = storage_service.get_url(image_keys[0]) if image_keys else image_urls[0]
        body_html = (
            f'<div class="image-gallery" style="margin:16px 0;">'
            f'<div class="gallery-main" style="width:100%;background:#f0f0f0;border-radius:8px;overflow:hidden;'
            f'display:flex;align-items:center;justify-content:center;min-height:300px;">'
            f'<img src="{first_url}" '
            f'style="max-width:100%;max-height:65vh;width:auto;height:auto;object-fit:contain;" />'
            f'</div>'
            f'<div style="display:flex;gap:8px;margin-top:12px;overflow-x:auto;padding:4px 0;">{thumbs}</div></div>'
        )
    else:
        body_html = "\n".join(
            f'<img src="{u}" style="width:100%;max-width:640px;border-radius:8px;display:block;margin:16px auto;" />'
            for u in image_urls
        )

    # Save version
    v = ContentVersion(tenant_id=job.tenant_id, job_id=job.id, version_number=1,
                       title=topic, body_markdown=body_md, body_html=body_html, summary="")
    db.add(v)

    # Publish to WeChat
    pm = config.get("publish_mode", "")
    aids = config.get("account_ids", [])
    if pm in ("draft", "direct") and aids:
        for aid in aids:
            try:
                _save_images_to_wechat_draft(db, job, aid, topic, body_md, mode=pm)
            except Exception as e:
                print(f"  ⚠️ 微信发布失败 account={aid}: {e}")

    job.status = "published"
    db.commit()
    print(f"  ✅ 完成: {len(assets)} 张图, 画廊={is_gallery}")
    return {"image_count": len(assets), "image_urls": [storage_service.get_url(k) for k in image_keys], "is_gallery": is_gallery}


def _create_asset(db: Session, job: ContentJob, asset_type: str,
                  storage_key: str, file_format: str = "",
                  file_size: int = 0, width: int = 0, height: int = 0,
                  duration_sec: int = 0, sort_order: int = 0,
                  phase: str = "completed", generation_config: Optional[dict] = None) -> ContentAsset:
    """创建 ContentAsset 记录"""
    asset = ContentAsset(
        tenant_id=job.tenant_id,
        job_id=job.id,
        content_type=job.content_type,
        asset_type=asset_type,
        storage_key=storage_key,
        file_format=file_format,
        file_size=file_size,
        width=width,
        height=height,
        duration_sec=duration_sec,
        sort_order=sort_order,
        version=1,
        phase=phase,
        generation_config=generation_config,
        created_by=job.created_by,
    )
    db.add(asset)
    db.flush()
    return asset


def _save_content_version(db: Session, job: ContentJob, slot: ContentJobArticle,
                          title: str, body_md: str, summary: str,
                          asset_keys: Optional[list] = None) -> ContentVersion:
    """创建 ContentVersion 记录"""
    version = ContentVersion(
        tenant_id=job.tenant_id,
        job_id=job.id,
        version_number=1,
        title=title,
        body_markdown=body_md,
        summary=summary,
        article_content_type=job.content_type,
        source="agent",
        created_by=job.created_by,
    )
    db.add(version)
    db.flush()
    return version


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def process_image_job(self, job_id: int):
    """纯图片生成流水线：生成 2-3 张 AI 图片（不带文字），保存到微信草稿箱"""
    db = MysqlSessionLocal()
    try:
        job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        # 图片生成同样可能被 Broker 重投。领取操作必须是条件更新，避免同一任务并发生成
        # 多组图片并重复写入素材库或公众号草稿。
        job = claim_dispatched_job_for_execution(db, job_id)
        if not job:
            current = db.query(ContentJob).filter(ContentJob.id == job_id).first()
            return {"job_id": job_id, "status": current.status if current else "missing", "ignored": True}

        from app.services.image_generation_service import image_generation_service
        from app.services.storage_service import generate_object_key, storage_service
        from app.services.asset_archive_service import save_image_to_asset_library

        config = job.generation_config or {}
        image_count = config.get("image_count", 3)
        topic = job.topic or ""

        import asyncio

        async def _run():
            job.status = "generating"
            db.commit()

            size_map = {"1:1": "1024*1024", "3:4": "1024*1365", "9:16": "1024*1820", "16:9": "1820*1024", "4:3": "1365*1024"}
            img_size = size_map.get(config.get("aspect_ratio", "4:3"), "1365*1024")

            image_urls = []
            image_keys = []
            assets = []

            # 生成 3 张主题相关图片（不带文字）
            prompts = [
                f"{topic}，宽景构图，柔和自然光线，干净留白背景，专业摄影，高级质感",
                f"{topic}，细节特写，质感丰富，浅景深，柔和光影",
                f"{topic}，场景氛围，自然光线，干净构图，温暖色调",
            ]

            for i in range(image_count):
                full_prompt = prompts[i] if i < len(prompts) else f"{topic}，干净构图，柔和光线"
                logger.info("Generating image %d/%d...", i + 1, image_count)

                img_url = None
                try:
                    img_url = await image_generation_service.generate_image(
                        full_prompt,
                        size=img_size,
                        tenant_id=job.tenant_id,
                    )
                except Exception as img_err:
                    logger.warning("Image %d generation failed: %s", i + 1, img_err)

                if img_url:
                    try:
                        asset = await save_image_to_asset_library(
                            db, job.tenant_id, img_url,
                            keywords=topic[:50], usage_type="generated_image",
                        )
                        assets.append(asset)
                        image_urls.append(img_url)
                        image_keys.append(asset.storage_key)
                    except Exception as save_err:
                        logger.warning("Image %d save failed: %s", i + 1, save_err)

                _create_asset(db, job, "final_image",
                             storage_key=asset.storage_key if i < len(assets) else "",
                             file_format="jpg", sort_order=i,
                             generation_config={"prompt": full_prompt, "seq": i})
                db.commit()

            if not assets:
                raise RuntimeError("All image generations failed")

            # 发布到微信（草稿箱或直接发布）
            account_ids = config.get("account_ids", [])
            publish_mode = config.get("publish_mode", "")
            logger.info("Image job %d: publish_mode=%s account_ids=%s topic=%s",
                       job_id, publish_mode, account_ids, topic)
            if publish_mode in ("draft", "direct") and account_ids:
                body_md = "\n\n".join(f"![]({storage_service.get_url(k)})" for k in image_keys)
                for aid in account_ids:
                    try:
                        _save_images_to_wechat_draft(db, job, aid, topic, body_md, mode=publish_mode)
                    except Exception as pub_err:
                        import traceback
                        logger.warning("WeChat publish failed for account %d: %s\n%s",
                                      aid, pub_err, traceback.format_exc())

            job.status = "published"
            db.commit()

            return {
                "job_id": job_id,
                "main_title": topic,
                "image_count": len(assets),
                "image_urls": [storage_service.get_url(k) for k in image_keys],
                "status": "completed",
            }

        result = asyncio.run(_run())
        return result

    except Exception as exc:
        logger.error("Image job %d failed: %s", job_id, exc)
        try:
            job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:500]
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


def _save_images_to_wechat_draft(db, job, account_id, title, body_markdown, mode="draft"):
    """将多张图片发布到微信（草稿箱或直接发布）

    当内容为纯图片时，自动使用画廊排版（主图 + 缩略图横排）。
    """
    from app.services.wechat_publisher import publish_article
    from app.models.mysql_models import Article as ArtModel

    import re

    # 从 markdown 中提取所有图片 URL
    image_urls = re.findall(r'!\[.*?\]\((.*?)\)', body_markdown or "")
    cover_image = image_urls[0] if image_urls else ""

    # 判断是否为纯图片内容（所有非空行都是 ![](url)）
    lines = [l.strip() for l in (body_markdown or "").split('\n') if l.strip()]
    is_pure_images = len(lines) > 0 and all(l.startswith('![') for l in lines)

    if is_pure_images and len(image_urls) > 1:
        # 顺序排版：所有图片按顺序排列
        imgs = "\n".join(
            f'<img src="{u}" style="width:100%;max-width:640px;border-radius:8px;'
            f'display:block;margin:16px auto;" />'
            for u in image_urls
        )
        gallery_html = f'<div style="margin:16px 0;">{imgs}</div>'
        content_for_wechat = gallery_html
    else:
        # 单张或非纯图片，使用原始 markdown
        content_for_wechat = body_markdown or ""

    article = ArtModel(
        task_id=f"img_{job.id}",
        tenant_id=job.tenant_id,
        main_title=title,
        content=content_for_wechat,
        full_content=content_for_wechat,
        cover_image=cover_image,
    )

    result = publish_article(db, article, account_id, mode=mode,
                             tenant_id=job.tenant_id, actor_id=job.created_by or 0)
    logger.info("WeChat publish result for account %d (mode=%s): %s", account_id, mode,
                {k: v for k, v in result.items() if k in ("media_id", "publish_id")})


def _save_video_to_wechat(db, job, account_id, title, video_key, mode="draft"):
    """将视频发布到微信（草稿箱或直接发布）"""
    from app.services.wechat_publisher import publish_article
    from app.models.mysql_models import Article as ArtModel
    from app.services.storage_service import storage_service as _ss

    video_url = _ss.get_url(video_key)
    cover_url = ""

    # 尝试获取封面
    cover_asset = db.query(ContentAsset).filter(
        ContentAsset.job_id == job.id,
        ContentAsset.asset_type == "cover",
    ).first()
    if cover_asset:
        cover_url = _ss.get_url(cover_asset.storage_key)

    article = ArtModel(
        task_id=f"vid_{job.id}",
        tenant_id=job.tenant_id,
        main_title=title,
        content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        full_content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        cover_image=cover_url,
    )

    publish_article(db, article, account_id, mode=mode,
                    tenant_id=job.tenant_id, actor_id=job.created_by or 0)
    logger.info("Video published to WeChat account %d (mode=%s)", account_id, mode)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def process_video_job(self, job_id: int):
    """视频生成流水线"""
    db = MysqlSessionLocal()
    try:
        job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        # 视频生成成本更高，必须与文章/图片共用同一领取协议，重复消息只能有一个 Worker 胜出。
        job = claim_dispatched_job_for_execution(db, job_id)
        if not job:
            current = db.query(ContentJob).filter(ContentJob.id == job_id).first()
            return {"job_id": job_id, "status": current.status if current else "missing", "ignored": True}

        from app.services.video_script_service import generate_video_script
        from app.services.image_generation_service import image_generation_service
        from app.services.tts_service import tts_service
        from app.services.video_composition_service import video_composition_service

        config = job.generation_config or {}
        total_duration = config.get("duration_sec", 30)
        storyboard_count = config.get("storyboard_count", 5)
        aspect_ratio = config.get("aspect_ratio", "9:16")
        logo_key = config.get("logo_image_key")
        qr_key = config.get("qr_code_image_key")

        import asyncio

        async def _run():
            job.status = "generating"
            db.commit()
            print(f"\n{'='*60}")
            print(f"  [视频任务 {job_id}] 开始处理")
            print(f"  ├─ 主题: {job.topic}")
            print(f"  ├─ 时长: {total_duration}s")
            print(f"  ├─ 分镜: {storyboard_count}")
            print(f"  ├─ 比例: {aspect_ratio}")
            print(f"{'='*60}")

            # Step 1: 生成脚本和分镜
            print(f"\n  >>> Step 1/3: 生成脚本和分镜 <<<")
            script = await generate_video_script(
                job.topic or "",
                total_duration=total_duration,
                storyboard_count=storyboard_count,
                aspect_ratio=aspect_ratio,
                target_audience=config.get("target_audience", ""),
                brand_style=config.get("brand_style", "专业"),
                extra_notes=config.get("extra_notes", ""),
            )
            print(f"  ✅ 脚本生成完成: {len(script.storyboards)} 个分镜")

            # Step 2: 生成每个分镜的图片
            print(f"\n  >>> Step 2/3: 生成分镜图片 ({storyboard_count}张) <<<")
            size_map = {"9:16": "1024*1820", "16:9": "1820*1024"}
            img_size = size_map.get(aspect_ratio, "1024*1820")

            storyboard_keys = []
            duration_per_image = []

            for i, sb in enumerate(script.storyboards):
                print(f"\n  >>> 分镜 {i+1}/{len(script.storyboards)} <<<")
                print(f"  ├─ prompt: {sb.image_prompt[:80] if sb.image_prompt else '默认'}")
                img_url = await image_generation_service.generate_image(
                    sb.image_prompt or f"{job.topic} {sb.visual_desc}",
                    size=img_size,
                    tenant_id=job.tenant_id,
                )
                if img_url:
                    from app.services.asset_archive_service import save_image_to_asset_library
                    asset = await save_image_to_asset_library(
                        db, job.tenant_id, img_url,
                        keywords=f"storyboard_{sb.seq}",
                        usage_type="video_storyboard",
                    )
                    key = asset.storage_key if asset else ""
                    storyboard_keys.append(key)
                    print(f"  ✅ 分镜 {i+1} 生成成功")

                    _create_asset(db, job, "storyboard_image", key,
                                 file_format="jpg", sort_order=sb.seq,
                                 duration_sec=sb.duration_sec,
                                 generation_config={"prompt": sb.image_prompt, "seq": sb.seq})
                else:
                    print(f"  ⚠️ 分镜 {i+1} 生成失败，使用占位图")
                    # 占位
                    placeholder_key = f"content/{job.tenant_id}/placeholder_{uuid.uuid4().hex[:8]}.jpg"
                    try:
                        from PIL import Image
                        import io
                        w, h = (int(x) for x in img_size.split("*"))
                        img = Image.new("RGB", (w, h), (30, 40, 60))
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=60)
                        storage_service.upload_bytes(placeholder_key, buf.getvalue(), "image/jpeg")
                        storyboard_keys.append(placeholder_key)
                    except Exception:
                        pass

                duration_per_image.append(sb.duration_sec)
                subtitle_segments.append((sb.subtitle, sb.duration_sec))

                db.commit()

            if not storyboard_keys:
                raise RuntimeError("No storyboard images generated")

            print(f"\n  >>> Step 3/3: 合成视频 ({len(storyboard_keys)} 张图片) <<<")
            video_bytes = await video_composition_service.compose_video(
                storyboard_image_keys=storyboard_keys,
                audio_key=None,
                subtitle_segments=None,
                duration_per_image=duration_per_image,
                logo_key=logo_key,
                qr_code_key=qr_key,
                resolution="1080x1920" if "9:16" in aspect_ratio else "1920x1080",
            )

            if not video_bytes:
                raise RuntimeError("Video composition returned empty result")

            # Step 6: 上传视频到 MinIO
            video_key = generate_object_key(
                job.tenant_id, f"video_{uuid.uuid4().hex[:8]}.mp4", prefix="content",
            )
            storage_service.upload_bytes(video_key, video_bytes, "video/mp4")

            _create_asset(db, job, "video", video_key,
                         file_format="mp4", file_size=len(video_bytes),
                         duration_sec=total_duration, sort_order=0)
            db.commit()

            # Step 7: 提取封面帧
            try:
                cover_bytes = await video_composition_service.extract_cover_frame(video_key)
                if cover_bytes:
                    cover_key = generate_object_key(
                        job.tenant_id, f"cover_{uuid.uuid4().hex[:8]}.jpg", prefix="content",
                    )
                    storage_service.upload_bytes(cover_key, cover_bytes, "image/jpeg")
                    _create_asset(db, job, "cover", cover_key,
                                 file_format="jpg", file_size=len(cover_bytes))
                    db.commit()
            except Exception as exc:
                logger.warning("Cover extraction failed: %s", exc)

            # 发布到微信（草稿箱或直接发布）
            account_ids = config.get("account_ids", [])
            publish_mode = config.get("publish_mode", "")
            if publish_mode in ("draft", "direct") and account_ids:
                for aid in account_ids:
                    try:
                        _save_video_to_wechat(db, job, aid, script.title, video_key, publish_mode)
                    except Exception as pub_err:
                        import traceback
                        logger.warning("WeChat publish failed for account %d: %s\n%s",
                                      aid, pub_err, traceback.format_exc())

            # 更新 job 状态为 published（已完成）
            job.status = "published"
            db.commit()

            return {
                "job_id": job_id,
                "title": script.title,
                "video_key": video_key,
                "video_url": storage_service.get_url(video_key),
                "status": "completed",
            }

        result = asyncio.run(_run())
        logger.info("Video job %d completed: %s", job_id, result.get("title", ""))
        return result

    except Exception as exc:
        logger.error("Video job %d failed: %s", job_id, exc)
        try:
            job = db.query(ContentJob).filter(ContentJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:500]
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
