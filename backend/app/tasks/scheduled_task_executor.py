"""Unified scheduled task executor — directly calls the same agent pipeline as article creation."""

import asyncio
import logging
import re
import uuid
from datetime import datetime

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask

logger = logging.getLogger(__name__)


@celery_app.task
def check_scheduled_tasks():
    """Periodic task: check scheduled tasks that need to execute now."""
    db = MysqlSessionLocal()
    try:
        import zoneinfo
        shanghai_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now_shanghai = datetime.now(shanghai_tz)
        today = now_shanghai.date()
        day_of_week = today.weekday()
        current_hour_min = f"{now_shanghai.hour:02d}:{now_shanghai.minute:02d}"

        tasks = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.is_active == True,
                ScheduledTask.day_of_week.in_([day_of_week, -1]),
            )
            .all()
        )

        triggered = 0
        for task in tasks:
            if not task.publish_times:
                continue

            account_ids = task.account_ids or ([task.account_id] if task.account_id else [])
            if not account_ids:
                continue

            for pub_time in task.publish_times:
                if pub_time and pub_time <= current_hour_min:
                    if task.last_run_at and task.last_run_at.date() == today:
                        logger.debug("Task %d already triggered today, skipping", task.id)
                        break

                    logger.info("Triggering scheduled task %d: %s", task.id, (task.topic or task.name)[:60])
                    execute_scheduled_article.delay(task.id)
                    triggered += 1
                    task.last_run_at = now_shanghai
                    break

        if triggered:
            db.commit()

        logger.info("Scheduled tasks: %d due tasks, %d jobs created", len(tasks), triggered)
        return {"tasks_checked": len(tasks), "jobs_created": triggered}

    except Exception as exc:
        logger.error("Scheduled task check failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def execute_scheduled_article(self, task_id: int):
    """直接调用和创建文章相同的 agent 流水线生成内容并发布。"""
    from app.models.mysql_models import Article, ScheduledTask as ST
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_service import create_article as create_article_record
    from app.services.wechat_publisher import publish_article
    from app.config import settings

    db = MysqlSessionLocal()
    try:
        task = db.query(ST).filter(ST.id == task_id).first()
        if not task:
            logger.error("Scheduled task %d not found", task_id)
            return {"error": f"Task {task_id} not found"}

        # 用户没提供主题时，不给兜底值 — 让具体处理函数自行决定（仿写标题或回退任务名）
        topic = task.topic  # 可能为 None
        fallback_topic = task.name
        content_type = task.content_type or "article"
        account_ids = task.account_ids or ([task.account_id] if task.account_id else [])
        publish_mode = task.publish_mode or "draft"

        print(f"\n{'='*60}")
        print(f"  [定时任务 {task_id}] content_type={content_type} topic={topic or '(用户未设置)'}")
        print(f"  accounts={account_ids} mode={publish_mode}")
        print(f"{'='*60}")

        # ========== 纯图片 ==========
        if content_type in ("image", "pure_image"):
            _scheduled_image(db, task, topic, fallback_topic, account_ids, publish_mode)

        # ========== 视频 ==========
        elif content_type == "video":
            _scheduled_video(db, task, topic, fallback_topic, account_ids, publish_mode)

        # ========== 图文 ==========
        else:
            _scheduled_article(db, task, topic, fallback_topic, account_ids, publish_mode)

        # 更新定时任务状态（db 可能因之前的异常处于 rollback 状态，捕获处理）
        try:
            task.total_generated = (task.total_generated or 0) + (task.articles_per_day or 1)
            task.last_run_at = datetime.utcnow()
            db.commit()
        except Exception as update_exc:
            logger.warning("Failed to update task progress: %s", update_exc)
            try:
                db.rollback()
            except Exception:
                pass

        logger.info("Scheduled task %d completed", task_id)
        return {"task_id": task_id, "status": "completed"}

    except Exception as exc:
        logger.error("Scheduled task %d failed: %s", task_id, exc)
        import traceback
        traceback.print_exc()
        return {"task_id": task_id, "error": str(exc)}
    finally:
        db.close()


def _scheduled_article(db, task, topic, fallback_topic, account_ids, publish_mode):
    """图文类型：和创建文章完全相同的 agent 流水线"""
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_service import create_article as create_article_record
    from app.services.article_agent_service import (
        agent1_generate_title_options,
        agent2_generate_outline,
        agent3_generate_content,
        agent4_analyze_image_requirements,
        agent5_generate_images,
        merge_images_into_content,
    )
    from app.services.wechat_publisher import publish_article
    from app.config import settings

    has_feed_source = task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id)
    if not topic and not has_feed_source:
        print(f"  ⚠️ 无主题且无投喂源，跳过图文生成")
        return

    for slot_idx in range(task.articles_per_day or 1):
        print(f"\n  >>> 槽位 {slot_idx+1} (图文) <<<")

        # 1. 创建 Article 记录
        article = create_article_record(
            db=db, user_id=task.created_by or 0, tenant_id=task.tenant_id,
            topic=topic or "", style=task.style or "default",
            image_source=task.image_source or "pexels",
            footer_template=task.footer_template,
        )
        print(f"  文章创建: task_id={article.task_id}")

        # 2. 构建 ArticleState
        state = ArticleState(
            task_id=article.task_id,
            user_id=task.created_by or 0,
            topic=topic or "",
            style=task.style or "default",
            enabled_image_methods=task.enabled_image_methods or ["PEXELS", "DASHSCOPE"],
            footer_template=task.footer_template,
        )

        # 3. 加载投喂源（仿写模式）
        ref_image_urls = []  # 收集参考文章中的图片 URL，用于后续 AI 生成配图
        if task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id):
            try:
                from app.models.mysql_models import FeedSource, FeedSourceArticle

                if task.feed_article_ids:
                    refs = db.query(FeedSourceArticle).filter(
                        FeedSourceArticle.id.in_(task.feed_article_ids),
                        FeedSourceArticle.body_markdown.isnot(None),
                    ).all()
                    if refs:
                        ref_texts = []
                        for r in refs:
                            ref_texts.append(f"## {r.title or '参考文章'}\n\n{r.body_markdown or ''}")
                            # 提取参考文章中的图片 URL
                            ref_image_urls.extend(re.findall(r'!\[.*?\]\((.*?)\)', r.body_markdown or ""))
                        state.reference_articles = ref_texts
                        print(f"  📄 已加载 {len(ref_texts)} 篇用户选中的参考文章，{len(ref_image_urls)} 张参考图片")
                        _load_layout_template(state, refs[0])

                    if task.feed_source_id:
                        src = db.query(FeedSource).filter(FeedSource.id == task.feed_source_id).first()
                        if src and src.style_profile:
                            state.style_profile = src.style_profile
                            print(f"  🎯 已加载仿写风格: {src.name}")
                else:
                    source_ids = task.feed_source_ids or ([task.feed_source_id] if task.feed_source_id else [])
                    if source_ids:
                        sources = db.query(FeedSource).filter(
                            FeedSource.id.in_(source_ids)
                        ).all()
                        for s in sources:
                            if s and s.style_profile:
                                state.style_profile = s.style_profile
                                print(f"  🎯 已加载仿写风格: {s.name}")
                                break
                        refs = db.query(FeedSourceArticle).filter(
                            FeedSourceArticle.feed_source_id.in_(source_ids),
                            FeedSourceArticle.body_markdown.isnot(None),
                        ).order_by(FeedSourceArticle.id.desc()).limit(3).all()
                        if refs:
                            ref_texts = []
                            for r in refs:
                                # 保留 [IMAGE:] 标记和完整正文，让 AI 能看到排版格式
                                body = r.body_markdown or ""
                                if body and len(body) > 50:
                                    ref_texts.append(f"## {r.title or '参考文章'}\n\n{body}")
                                    # 提取参考文章中的图片 URL
                                    ref_image_urls.extend(re.findall(r'!\[.*?\]\((.*?)\)', body))
                            state.reference_articles = ref_texts
                            print(f"  📄 已加载 {len(ref_texts)} 篇参考文章，{len(ref_image_urls)} 张参考图片")
                        _load_layout_template(state, refs[0])
            except Exception as exc:
                print(f"  ⚠️ 加载投喂源失败: {exc}")

        # 4. 加载知识库
        if task.knowledge_base_ids:
            try:
                from app.database import get_pg_db
                from app.services.knowledge_base_service import search_knowledge_base
                pg_db = next(get_pg_db())
                try:
                    chunks = []
                    for kb_id in task.knowledge_base_ids:
                        results = search_knowledge_base(pg_db, kb_id, topic or "", top_k=3)
                        chunks.extend(results)
                    if chunks:
                        parts = [f"[来源: chunk_id={r['id']}]\n{r['content']}" for r in chunks]
                        state.kb_context = "\n\n---\n\n".join(parts)
                        print(f"  📚 已加载知识库: {len(chunks)} 个片段")
                finally:
                    pg_db.close()
            except Exception as exc:
                print(f"  ⚠️ 知识库加载失败: {exc}")

        # 5. 异步运行 agent 流水线
        def _run_pipeline(init_state):
            import asyncio
            async def _run():
                s = init_state

                # Agent 1: 标题 — 返回 ArticleState
                s = await agent1_generate_title_options(s)
                s.title = s.title_options[0] if s.title_options else SelectedTitle(main_title=topic or (s.reference_articles[0] if s.reference_articles else ""), sub_title="")

                # Agent 2: 大纲
                s = await agent2_generate_outline(s)

                # Agent 3: 正文
                s = await agent3_generate_content(s)

                # 配图: 有参考图片时走理解+AI生成，否则走 Pexels 搜索
                # 注意: 有 layout_template 时，agent 3 已按模板生成 [IMAGE:] 占位符
                # agent4/5 直接解析并配图即可
                if settings.dashscope_api_key:
                    if s.layout_template and s.content_blocks:
                        # 路径①: 结构化模板 → 从 blocks 提取图片需求 → 配图 → 渲染
                        from app.services.article_agent_service import (
                            extract_image_slots_from_blocks,
                            render_final_content,
                        )
                        from app.schemas.article import ImageRequirement

                        image_slots, s.content_blocks = extract_image_slots_from_blocks(s.content_blocks)

                        s.image_requirements = [
                            ImageRequirement(
                                position=slot["position"],
                                type="inline",
                                keywords=slot["requirement"],
                                prompt=slot["requirement"],
                                image_source="DASHSCOPE",
                                placeholder_id=slot["slot_id"],
                            )
                            for slot in image_slots
                        ]

                        s = await agent5_generate_images(s)

                        slot_urls = {}
                        for i, img in enumerate(s.images):
                            if img.url and i < len(image_slots):
                                slot_urls[image_slots[i]["slot_id"]] = img.url

                        s.full_content = render_final_content(
                            s.content_blocks,
                            slot_urls,
                            footer_template=s.footer_template or "",
                        )
                    elif ref_image_urls:
                        # 路径②: 有参考图片 → 理解+AI 生成
                        await _gen_images_from_references(s, ref_image_urls)
                        merge_images_into_content(s)
                    else:
                        # 路径③: 走 Pexels 搜索
                        s = await agent4_analyze_image_requirements(s)
                        s = await agent5_generate_images(s)
                        merge_images_into_content(s)
                else:
                    s.full_content = s.content or ""

                return s

            return asyncio.run(_run())

        state = _run_pipeline(state)

        # 6. 更新 Article
        title_text = state.title.main_title if state.title else (topic or "")
        article.main_title = title_text
        article.content = state.full_content or state.content or ""
        article.full_content = state.full_content or state.content or ""

        # 封面：取正文第一张图片（支持 Markdown 和 HTML 格式）
        _cover_matches = re.findall(r'!\[.*?\]\((.*?)\)', article.full_content or "")
        if not _cover_matches:
            _cover_matches = re.findall(r'<img[^>]+src="([^"]+)"', article.full_content or "")
        if _cover_matches:
            article.cover_image = _cover_matches[0]
            print(f"  🖼️ 封面: {_cover_matches[0][:60]}")

        article.status = "published" if publish_mode == "direct" else "draft_saved"
        article.phase = "PUBLISHED" if publish_mode == "direct" else "DRAFT_SAVED"
        db.commit()

        print(f"  ✅ 内容生成完成: {title_text[:40]}")

        # 7. 发布到微信
        _publish_to_wechat(db, article, account_ids, publish_mode, task)

    db.commit()
    print(f"\n  ✅ 图文任务完成")


async def _gen_images_from_references(state, ref_image_urls):
    """使用参考图片理解 + AI 生成配图，替代 Pexels 搜索"""
    import re as _re
    from app.services.wanxiang_service import WanxiangImageService
    from app.agent.nodes.image_understanding_node import understand_images
    from app.agent.nodes.prompt_crafting_node import craft_prompt
    from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
    from app.schemas.article import ImageResult

    if not ref_image_urls:
        print(f"  ⚠️ 无参考图片，跳过 AI 配图")
        return

    print(f"  ▶ 理解参考图片（{len(ref_image_urls)} 张）...")
    try:
        visual_descs = understand_images(ref_image_urls)
    except Exception as e:
        print(f"  ⚠️ 图片理解失败: {e}")
        return

    if not visual_descs:
        print(f"  ⚠️ 图片理解未返回描述")
        return

    # 过滤掉二维码图片（不生成配图）
    qrcode_count = sum(1 for d in visual_descs if d.get("is_qrcode"))
    if qrcode_count:
        visual_descs = [d for d in visual_descs if not d.get("is_qrcode")]
        print(f"  🚫 过滤掉 {qrcode_count} 张二维码图片，剩余 {len(visual_descs)} 张参考")
    if not visual_descs:
        print(f"  ⚠️ 所有参考图片均为二维码，跳过 AI 配图")
        return

    # 从正文中提取 [IMAGE:] 占位符
    placeholders = list(_re.finditer(r'\[IMAGE:position=(\d+),keywords=([^,\]]+),type=([^\]]+)\]', state.content or ""))
    if not placeholders:
        print(f"  ⚠️ 正文中无 [IMAGE:] 占位符")
        return

    print(f"  ▶ AI 生成配图（{len(placeholders)} 张）...")

    async def _gen_all():
        wanxiang = WanxiangImageService()
        results = []
        for idx, m in enumerate(placeholders):
            pos = int(m.group(1))
            orig_kw = m.group(2)
            img_type = m.group(3)

            desc_idx = idx % len(visual_descs)
            desc = visual_descs[desc_idx]
            try:
                pd = craft_prompt(desc, topic=orig_kw, similarity="medium")
                prompt = pd["prompt"]
            except Exception:
                prompt = build_wanxiang_prompt(desc, orig_kw, "medium")

            print(f"    >>> 图片 {idx+1}/{len(placeholders)} (pos={pos}) <<<")
            img_url = await wanxiang.generate_image(prompt, size="1024*1365")
            results.append(ImageResult(position=pos, url=img_url or "", method="WANXIANG_IMITATE", keywords=orig_kw, section_title="", description=str(desc), placeholder_id=m.group(0)))
            if img_url:
                print(f"      ✅ 图片 {idx+1}")
            else:
                print(f"      ⚠️ 图片 {idx+1} 生成失败")
        return results

    state.images = await _gen_all()
    success = len([img for img in state.images if img.url])
    print(f"  ✅ AI 配图完成: {success}/{len(placeholders)} 张")


def _scheduled_image(db, task, topic, fallback_topic, account_ids, publish_mode):
    """纯图片类型：和创建文章完全相同的图片生成流程"""
    import asyncio
    import re as _re
    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.storage_service import storage_service
    from app.services.wechat_publisher import publish_article
    from app.services.wanxiang_service import WanxiangImageService
    from app.models.mysql_models import Article

    print(f"\n  >>> 纯图片 <<<")

    # === 有投喂源：使用 Agent 仿写流程 ===
    ref = None
    task_feed_article_ids = task.feed_article_ids or []
    has_feed_source = task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id)

    if task_feed_article_ids:
        # 用户选了具体文章 — 直接取
        from app.models.mysql_models import FeedSourceArticle as FSA
        refs = db.query(FSA).filter(FSA.id.in_(task_feed_article_ids)).all()
        if refs:
            ref = refs[0]
            print(f"  [纯图片仿写] 选中文章: {ref.title}")
    elif has_feed_source:
        # 用户没选具体文章 — 从投喂源取最近有图的文章
        from app.models.mysql_models import FeedSourceArticle as FSA
        source_ids = task.feed_source_ids or ([task.feed_source_id] if task.feed_source_id else [])
        if source_ids:
            candidates = db.query(FSA).filter(
                FSA.feed_source_id.in_(source_ids),
                FSA.body_markdown.isnot(None),
            ).order_by(FSA.id.desc()).limit(5).all()
            for c in candidates:
                imgs = _re.findall(r'!\[.*?\]\((.*?)\)', c.body_markdown or "")
                if imgs:
                    ref = c
                    print(f"  [纯图片仿写] 取投喂源文章: {ref.title} ({len(imgs)} 张图)")
                    break

    if ref is not None:
        # Agent 仿写流程 — 视觉理解参考图片 → 生成新图
        try:
            from app.agent.nodes.title_imitation_node import imitate_title
            from app.agent.nodes.image_understanding_node import understand_images
            from app.agent.nodes.prompt_crafting_node import craft_prompt
            from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt

            ref_title = ref.title or ""
            ref_body = ref.body_markdown or ""
            image_urls_from_ref = _re.findall(r'!\[.*?\]\((.*?)\)', ref_body)
            print(f"  提取图片: {len(image_urls_from_ref)} 张")

            if not image_urls_from_ref:
                print(f"  ⚠️ 参考文章中没有图片，跳过")
                return

            # Agent 1: 标题（用用户主题或仿写，不兜底任务名称）
            new_title = topic  # 用户没设主题时就是 None，走仿写
            if not new_title:
                titles = imitate_title(ref_title, topic="", count=3)
                new_title = titles[0] if titles else ref_title
            print(f"  标题: {new_title}")

            # Agent 3: 视觉理解参考图片
            visual_descs = understand_images(image_urls_from_ref)
            if not visual_descs:
                print(f"  ⚠️ 图片理解无返回")
                return

            # 过滤掉二维码图片
            qrcode_count = sum(1 for d in visual_descs if d.get("is_qrcode"))
            if qrcode_count:
                visual_descs = [d for d in visual_descs if not d.get("is_qrcode")]
                print(f"  🚫 过滤掉 {qrcode_count} 张二维码图片，剩余 {len(visual_descs)} 张参考")
            if not visual_descs:
                print(f"  ⚠️ 所有参考图片均为二维码，跳过")
                return

            # Agent 4+5: 逐张生成（数量 = 参考图片数量）
            async def _run_image_gen():
                wanxiang = WanxiangImageService()
                gen_urls = []
                gen_keys = []
                for i, desc in enumerate(visual_descs):
                    print(f"\n  >>> 图片 {i+1}/{len(visual_descs)} <<<")
                    pd = craft_prompt(desc, topic=new_title, similarity="medium")
                    prompt = pd["prompt"]
                    if not prompt:
                        prompt = build_wanxiang_prompt(desc, new_title, "medium")
                    print(f"  生成 prompt ({len(prompt)}字): {prompt[:200]}")
                    img_url = await wanxiang.generate_image(prompt, size="1024*1365")
                    if img_url:
                        asset = await save_image_to_asset_library(
                            db, task.tenant_id, img_url, keywords=new_title[:50],
                        )
                        gen_urls.append(img_url)
                        if asset:
                            gen_keys.append(asset.storage_key)
                        print(f"  ✅ 图片 {i+1} 生成成功")
                    else:
                        print(f"  ⚠️ 图片 {i+1} 生成失败")
                return gen_urls, gen_keys

            image_urls, image_keys = asyncio.run(_run_image_gen())

            if not image_urls:
                print(f"  ❌ 所有图片生成失败")
                return

            body_md = "\n\n".join(f"![]({storage_service.get_url(k)})" for k in image_keys)

            article = Article(
                task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
                tenant_id=task.tenant_id,
                main_title=new_title,
                content=body_md,
                full_content=body_md,
                cover_image=storage_service.get_url(image_keys[0]),
                status="published" if publish_mode == "direct" else "draft_saved",
                phase="PUBLISHED" if publish_mode == "direct" else "DRAFT_SAVED",
            )
            db.add(article)
            db.flush()

            _publish_to_wechat(db, article, account_ids, publish_mode, task)
            print(f"  ✅ 纯图片仿写完成: {len(image_urls)} 张图")
            return

        except Exception as e:
            print(f"  ❌ 仿写流程失败: {e}")
            import traceback
            traceback.print_exc()
            return

    # === 无投喂源：通用图片生成（3 张） ===
    # 有主题用主题，无主题就不生成（不兜底任务名称）
    if not topic:
        print(f"  ⚠️ 无主题且无投喂源，跳过纯图片生成")
        return
    async def _run():
        wanxiang = WanxiangImageService()
        prompts = [
            f"{topic}，宽景构图，柔和自然光线，干净留白背景，专业摄影，高级质感。不要包含任何文字或文本标签，纯图像。",
            f"{topic}，细节特写，质感丰富，浅景深，柔和光影。不要包含任何文字或文本标签，纯图像。",
            f"{topic}，场景氛围，自然光线，干净构图，温暖色调。不要包含任何文字或文本标签，纯图像。",
        ]

        image_urls = []
        image_keys = []

        for i in range(3):
            print(f"  ▶ 生成图片 {i+1}/3...")
            try:
                img_url = await wanxiang.generate_image(prompts[i], size="1024*1365")
                if img_url:
                    asset = await save_image_to_asset_library(
                        db, task.tenant_id, img_url,
                        keywords=topic[:50], usage_type="generated_image",
                    )
                    image_urls.append(img_url)
                    if asset:
                        image_keys.append(asset.storage_key)
                    print(f"    ✅ 图片 {i+1}")
                else:
                    print(f"    ⚠️ 图片 {i+1} 为空")
            except Exception as e:
                print(f"    ⚠️ 图片 {i+1} 失败: {e}")

        return image_urls, image_keys

    image_urls, image_keys = asyncio.run(_run())

    if not image_urls:
        print(f"  ❌ 所有图片生成失败")
        return

    body_md = "\n\n".join(f"![]({storage_service.get_url(k)})" for k in image_keys)

    article = Article(
        task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
        tenant_id=task.tenant_id,
        main_title=topic,
        content=body_md,
        full_content=body_md,
        cover_image=storage_service.get_url(image_keys[0]),
        status="published" if publish_mode == "direct" else "draft_saved",
        phase="PUBLISHED" if publish_mode == "direct" else "DRAFT_SAVED",
    )
    db.add(article)
    db.flush()

    _publish_to_wechat(db, article, account_ids, publish_mode, task)
    print(f"  ✅ 纯图片完成: {len(image_urls)} 张图")


def _scheduled_video(db, task, topic, fallback_topic, account_ids, publish_mode):
    """视频类型：和创建文章完全相同的视频生成流程"""
    import asyncio
    import uuid as _uuid
    from app.models.mysql_models import Article
    from app.services.storage_service import generate_object_key as _gen_key, storage_service as _ss
    from app.services.wechat_publisher import publish_article
    from app.services.video_gen_service import video_gen_service as _vgen
    from app.config import settings

    use_topic = topic or fallback_topic
    print(f"\n  >>> 视频 <<<")
    print(f"  [视频] 开始处理: {use_topic}")

    async def _run():
        dur = settings.video_duration_sec if hasattr(settings, 'video_duration_sec') else 5
        ar = "9:16"
        size = "720*1280"
        prompt = use_topic

        print(f"  >>> 提交文生视频: {prompt[:80]}")
        video_url = await _vgen.generate_video(prompt=prompt, size=size, duration=dur)
        if not video_url:
            raise RuntimeError("视频生成失败，请检查 API Key 是否有万相视频模型权限")

        print(f"  ✅ 视频生成完毕")

        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=120) as _client:
            resp = await _client.get(video_url)
            resp.raise_for_status()
            video_bytes = resp.content

        vk = _gen_key(task.tenant_id, f"video_{_uuid.uuid4().hex[:8]}.mp4", prefix="content")
        _ss.upload_bytes(vk, video_bytes, "video/mp4")
        vu = _ss.get_url(vk)
        print(f"  ✅ 视频已保存")

        # 生成封面
        cover_url = ""
        try:
            from app.services.wanxiang_service import WanxiangImageService as _WX
            cover_prompt = f"{use_topic}，封面图，视觉冲击力，高清，适合做视频封面"
            _wx = _WX()
            _cover_img_url = await _wx.generate_image(cover_prompt, size="720*1280")
            if _cover_img_url:
                from app.services.asset_archive_service import save_image_to_asset_library
                _asset = await save_image_to_asset_library(
                    db, task.tenant_id, _cover_img_url, keywords=f"video_cover",
                )
                if _asset and _asset.storage_key:
                    cover_url = _ss.get_url(_asset.storage_key)
                    print(f"  ✅ 封面已生成")
        except Exception as e:
            print(f"  ⚠️ 封面生成异常: {e}")

        return vu, cover_url

    video_url, cover_url = asyncio.run(_run())

    article = Article(
        task_id=f"sched_vid_{task.id}_{_uuid.uuid4().hex[:6]}",
        tenant_id=task.tenant_id,
        main_title=use_topic,
        content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        full_content=f'<p><video src="{video_url}" controls style="width:100%" /></p>',
        cover_image=cover_url,
        status="published" if publish_mode == "direct" else "draft_saved",
        phase="PUBLISHED" if publish_mode == "direct" else "DRAFT_SAVED",
    )
    db.add(article)
    db.flush()

    _publish_to_wechat(db, article, account_ids, publish_mode, task)
    print(f"  ✅ 视频完成")


def _publish_to_wechat(db, article, account_ids, publish_mode, task):
    """发布到微信"""
    from app.services.wechat_publisher import publish_article

    if not account_ids:
        print(f"  ⚠️ 未配置公众号，跳过发布")
        return

    for aid in account_ids:
        try:
            publish_article(db, article, aid, mode=publish_mode,
                            tenant_id=task.tenant_id, actor_id=task.created_by or 0)
            print(f"  ✅ 已{'直接发布' if publish_mode == 'direct' else '保存草稿'}到公众号 #{aid}")
        except Exception as e:
            print(f"  ⚠️ 发布到公众号 #{aid} 失败: {e}")


def _load_layout_template(state, feed_article) -> None:
    """Load LayoutTemplate from a FeedSourceArticle.analysis JSON field into state."""
    from app.schemas.article import LayoutTemplate

    if not feed_article or not feed_article.analysis:
        return

    analysis = feed_article.analysis
    if not isinstance(analysis, dict):
        return

    if analysis.get("layout_status") != "completed":
        return

    template_data = analysis.get("layout_template")
    if not template_data:
        return

    try:
        state.layout_template = LayoutTemplate(**template_data)
        section_count = len(state.layout_template.sections)
        print(f"  Template loaded: {section_count} sections, {state.layout_template.total_image_count} images")
    except Exception as exc:
        print(f"  Template parse failed: {exc}")
