"""Unified scheduled task executor — directly calls the same agent pipeline as article creation."""

import asyncio
import html
import logging
import re
import uuid
from datetime import datetime

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask, ScheduledTaskRun
from app.schemas.article import ImageResult
from app.services.scheduled_erp_image_policy import find_due_schedule_times

logger = logging.getLogger(__name__)


def _cleanup_cos_relay_objects(relay_service, object_keys: list[str]) -> None:
    """精确删除本次文章产生的 COS 临时对象。

    清理失败只记录告警，不覆盖图生图或公众号发布的主异常；对象键来自当前
    任务准备结果，不接受前缀，因此不会误删其他租户或其他运行批次的素材。
    """
    if relay_service is None:
        return
    for object_key in reversed(object_keys):
        try:
            relay_service.delete_object(object_key)
        except Exception as exc:
            logger.warning("COS 临时对象清理失败 key=%s: %s", object_key, exc)


async def _run_with_cos_cleanup(operation, relay_service, object_keys: list[str]):
    """无论文章流水线成功或失败，都在 finally 中释放 COS 中转对象。"""
    try:
        return await operation()
    finally:
        _cleanup_cos_relay_objects(relay_service, object_keys)


def _select_article_cover(state, full_content: str) -> str:
    """优先选择本次生成的第一张有效图片作为封面。

    ``state.images`` 能准确表达图片 Agent 的输出顺序，应优先于解析最终 HTML；
    只有旧流程没有图片元数据时才解析最终内容。最终发布正文以 HTML 为准，
    因此 HTML 图片必须优先于 Markdown 中可能残留的本地页脚或历史占位图。
    """
    for image in getattr(state, "images", []) or []:
        image_url = str(getattr(image, "url", "") or "").strip()
        if image_url:
            # 最终内容可能经过 HTML 序列化，签名 URL 中的 ``&`` 会变成
            # ``&amp;``；封面下载接口需要原始查询串，因此在边界统一还原。
            return html.unescape(image_url)

    html_match = re.search(
        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']',
        full_content or "",
        re.IGNORECASE,
    )
    if html_match:
        return html.unescape(html_match.group(1).strip())

    markdown_match = re.search(r'!\[.*?\]\((.*?)\)', full_content or "")
    return html.unescape(markdown_match.group(1).strip()) if markdown_match else ""


def is_completed_scheduled_run(run: ScheduledTaskRun | None) -> bool:
    """判断运行记录是否已成功交付，防止 Redis 重投重复创建草稿。

    Celery worker 重启或网络确认超时后，已确认的消息可能再次投递。仅当运行状态为
    ``completed`` 且已关联文章时才视为不可重执行，避免历史残缺记录被错误跳过。
    """
    return bool(
        run
        and str(getattr(run, "status", "")).lower() == "completed"
        and getattr(run, "article_id", None)
    )


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

            existing_times = {
                row[0]
                for row in db.query(ScheduledTaskRun.scheduled_time).filter(
                    ScheduledTaskRun.task_id == task.id,
                    ScheduledTaskRun.scheduled_date == today,
                ).all()
            }
            due_times = find_due_schedule_times(
                task.publish_times,
                now_shanghai.replace(tzinfo=None),
                existing_times,
                grace_minutes=5,
            )
            for schedule_time in due_times:
                # 唯一约束与独立提交共同防止 API 后台线程和 Celery Beat 并发重复触发。
                run = ScheduledTaskRun(
                    task_id=task.id,
                    scheduled_date=today,
                    scheduled_time=schedule_time,
                    status="queued",
                )
                db.add(run)
                try:
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning("Task %d slot %s was already claimed or could not be created: %s", task.id, schedule_time, exc)
                    continue

                logger.info("Triggering scheduled task %d at %s: %s", task.id, schedule_time, (task.topic or task.name)[:60])
                execute_scheduled_article.delay(task.id, run.id)
                triggered += 1

        logger.info("Scheduled tasks: %d due tasks, %d jobs created", len(tasks), triggered)
        return {"tasks_checked": len(tasks), "jobs_created": triggered}

    except Exception as exc:
        logger.error("Scheduled task check failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def execute_scheduled_article(self, task_id: int, run_id: int | None = None):
    """直接调用和创建文章相同的 agent 流水线生成内容并发布。"""
    from app.models.mysql_models import Article, ScheduledTask as ST
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_service import create_article as create_article_record
    from app.services.wechat_publisher import publish_article
    from app.config import settings

    # 用量账本必须覆盖 ERP 选图、标题、正文、图生图和发布前处理的完整一次运行；
    # 当前先写入 Worker 日志，后续接入账单表时无需侵入各 Agent。
    from app.services.model_usage_service import (
        begin_model_usage_collection,
        end_model_usage_collection,
    )

    usage_token = begin_model_usage_collection(
        f"scheduled_task:{task_id}:run:{run_id or 'manual'}"
    )
    db = MysqlSessionLocal()
    try:
        task = db.query(ST).filter(ST.id == task_id).first()
        if not task:
            logger.error("Scheduled task %d not found", task_id)
            return {"error": f"Task {task_id} not found"}

        # 任务可能由旧脚本创建而没有 ``created_by``。文章表的 user_id 是强外键，
        # 因此必须在整个运行开始时解析真实租户成员，并在所有后续步骤复用该身份。
        from app.services.scheduled_task_actor_service import resolve_scheduled_task_actor_id

        execution_actor_id = resolve_scheduled_task_actor_id(db, task)

        run = None
        if run_id is not None:
            run = db.query(ScheduledTaskRun).filter(
                ScheduledTaskRun.id == run_id,
                ScheduledTaskRun.task_id == task_id,
            ).first()
            if not run:
                return {"task_id": task_id, "error": f"Task run {run_id} not found"}
            if is_completed_scheduled_run(run):
                logger.info(
                    "跳过已完成的定时任务运行 task_id=%s run_id=%s article_id=%s",
                    task_id,
                    run.id,
                    run.article_id,
                )
                return {
                    "task_id": task_id,
                    "run_id": run.id,
                    "article_id": run.article_id,
                    "status": "skipped_completed",
                }
            run.status = "running"
            run.started_at = datetime.utcnow()
            db.commit()

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
            article_id = _scheduled_image(db, task, topic, fallback_topic, account_ids, publish_mode)

        # ========== 视频 ==========
        elif content_type == "video":
            article_id = _scheduled_video(db, task, topic, fallback_topic, account_ids, publish_mode)

        # ========== 图文 ==========
        else:
            article_id = _scheduled_article(
                db,
                task,
                topic,
                fallback_topic,
                account_ids,
                publish_mode,
                run_id,
                execution_actor_id,
            )

        # 更新定时任务状态（db 可能因之前的异常处于 rollback 状态，捕获处理）
        try:
            task.total_generated = (task.total_generated or 0) + (task.articles_per_day or 1)
            task.last_run_at = datetime.utcnow()
            if run:
                run.status = "completed" if article_id else "failed"
                run.article_id = article_id
                run.error_message = None if article_id else "未生成可发布文章"
                run.finished_at = datetime.utcnow()
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
        if run_id is not None:
            try:
                run = db.query(ScheduledTaskRun).filter(ScheduledTaskRun.id == run_id).first()
                if run:
                    run.status = "failed"
                    run.error_message = str(exc)[:4000]
                    run.finished_at = datetime.utcnow()
                    db.commit()
            except Exception:
                db.rollback()
        return {"task_id": task_id, "error": str(exc)}
    finally:
        db.close()
        usage = end_model_usage_collection(usage_token)
        logger.info(
            "模型用量汇总 scope=%s text_requests=%d input_tokens=%d "
            "output_tokens=%d total_tokens=%d image_requests=%d image_breakdown=%s",
            usage.scope,
            usage.text_request_count,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.image_request_count,
            list(usage.image_breakdown),
        )


def _scheduled_article(
    db,
    task,
    topic,
    fallback_topic,
    account_ids,
    publish_mode,
    run_id: int | None = None,
    execution_actor_id: int | None = None,
):
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
    from app.services.scheduled_erp_image_service import (
        parse_scheduled_erp_image_config,
        prepare_erp_images_for_scheduled_run,
    )
    from app.services.cos_image_relay_service import CosImageRelayService
    from app.services.scheduled_article_context_service import (
        ScheduledKnowledgeContextError,
        bind_product_context,
        ensure_product_name_in_title,
        load_required_knowledge_context,
        split_knowledge_prompt_context,
    )

    has_feed_source = task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id)
    has_knowledge_base = bool(task.knowledge_base_ids)
    if not topic and not has_feed_source and not has_knowledge_base:
        print(f"  ⚠️ 无主题、投喂源和知识库，跳过图文生成")
        return None

    erp_image_config = parse_scheduled_erp_image_config(task.erp_image_config)
    if erp_image_config and run_id is None:
        raise ValueError("ERP 分类配图只能通过已记录的定时时段执行")

    # 投喂源、ERP 和知识库的职责必须在加载内容前明确：投喂源始终可用于
    # 文章结构与文字风格，ERP 则会覆盖投喂源图片成为唯一视觉主体。将决策
    # 收敛到纯策略服务，可避免未来某个图片分支又绕过 ERP 优先级。
    from app.services.scheduled_image_routing_policy import resolve_scheduled_image_route

    image_route = resolve_scheduled_image_route(
        has_erp_product=erp_image_config is not None,
        has_feed_source=has_feed_source,
        has_knowledge_base=has_knowledge_base,
    )
    generated_article_id = None

    for slot_idx in range(task.articles_per_day or 1):
        print(f"\n  >>> 槽位 {slot_idx+1} (图文) <<<")

        # 1. 创建 Article 记录
        actor_id = execution_actor_id
        if actor_id is None:
            from app.services.scheduled_task_actor_service import resolve_scheduled_task_actor_id

            actor_id = resolve_scheduled_task_actor_id(db, task)

        article = create_article_record(
            db=db, user_id=actor_id, tenant_id=task.tenant_id,
            topic=topic or "", style=task.style or "default",
            image_source=task.image_source or "dashscope",
            footer_template=task.footer_template,
        )
        print(f"  文章创建: task_id={article.task_id}")

        # 2. 构建 ArticleState
        state = ArticleState(
            task_id=article.task_id,
            user_id=actor_id,
            tenant_id=task.tenant_id,
            topic=topic or "",
            style=task.style or "default",
            enabled_image_methods=task.enabled_image_methods or ["DASHSCOPE"],
            footer_template=task.footer_template,
        )
        # ERP 路径中，投喂源只仿写文章结构与文案；产品图片和知识库背景是唯一
        # 视觉输入。该显式状态会传到 HTML 仿写 Agent，避免它再分析原文章图片。
        state.skip_reference_image_understanding = image_route.mode == "erp_knowledge_background"

        # 3. 加载投喂源（仿写模式）。无论图片来源为何，文章文本、HTML 结构和
        # 风格档案都必须保留；但 ERP 模式禁止把投喂源图片送入视觉理解或仿写。
        ref_image_urls = []
        if task.writing_mode == "feed" and (task.feed_source_ids or task.feed_source_id):
            try:
                from app.models.mysql_models import FeedSource, FeedSourceArticle

                if task.feed_article_ids:
                    refs = db.query(FeedSourceArticle).filter(
                        FeedSourceArticle.id.in_(task.feed_article_ids),
                        FeedSourceArticle.body_markdown.isnot(None),
                    ).all()
                    if refs:
                        # 用户明确选中的第一篇文章决定 HTML 版式，其他文章只提供语言风格。
                        state.reference_html = refs[0].body_html or None
                        ref_texts = []
                        for r in refs:
                            reference_context = _build_reference_article_for_imitation(
                                r.title or "参考文章",
                                r.body_markdown or "",
                            )
                            if reference_context:
                                ref_texts.append(reference_context)
                            # ERP 产品优先时只仿写文章，不读取投喂源图片。这样
                            # 图片模型只能收到 ERP 原图和知识库的背景规则。
                            if image_route.load_reference_visuals:
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
                            # 自动选取时同样只采用一篇文章的 DOM，避免跨文章拼接版式。
                            state.reference_html = refs[0].body_html or None
                            ref_texts = []
                            for r in refs:
                                # 保留 [IMAGE:] 标记和完整正文，让 AI 能看到排版格式
                                body = r.body_markdown or ""
                                reference_context = _build_reference_article_for_imitation(
                                    r.title or "参考文章",
                                    body,
                                )
                                if reference_context and len(reference_context) > 50:
                                    ref_texts.append(reference_context)
                                    # 参考图片属于“AI 视觉仿写”专属输入，不能与 ERP
                                    # 产品图混用，否则模型会错误替换产品主体或背景规则。
                                    if image_route.load_reference_visuals:
                                        ref_image_urls.extend(re.findall(r'!\[.*?\]\((.*?)\)', body))
                            state.reference_articles = ref_texts
                            print(f"  📄 已加载 {len(ref_texts)} 篇参考文章，{len(ref_image_urls)} 张参考图片")
                        _load_layout_template(state, refs[0])
            except Exception as exc:
                print(f"  ⚠️ 加载投喂源失败: {exc}")

        # 4. 异步运行 Agent 流水线。ERP 产品和知识库会在标题 Agent 之前准备，
        # 因为产品名必须同时约束标题、正文和图片，而不是等图片槽位出现后才选图。
        relay_service = CosImageRelayService() if erp_image_config else None
        relay_object_keys: list[str] = []

        def _run_pipeline(init_state):
            import asyncio

            async def _run():
                s = init_state

                async def _prepare_product_and_knowledge_context() -> None:
                    """一次性准备整篇文章共用的 ERP 产品与品牌知识库上下文。

                    产品必须在标题生成前选定；知识库采用任务绑定的完整品牌规则，
                    不再依赖可能为空的主题向量检索。任何一项缺失都会停止发布，避免
                    生成一篇只有产品图、没有指定背景规则的文章。
                    """

                    product_name = ""
                    if erp_image_config:
                        prepared_images = await prepare_erp_images_for_scheduled_run(
                            db=db,
                            task_id=task.id,
                            tenant_id=task.tenant_id,
                            run_id=run_id,
                            config=erp_image_config,
                            # 一个 ERP 产品驱动整篇 4～5 张图片，防重记录也只占用一张原图。
                            requested_count=1,
                            relay_service=relay_service,
                        )
                        prepared_image = prepared_images[0]
                        relay_object_keys.append(prepared_image.relay_object_key)
                        s.reference_image_url = prepared_image.reference_url
                        s.reference_image_bytes = prepared_image.reference_image_bytes
                        s.reference_content_type = prepared_image.reference_content_type
                        # ERP 可能只返回产品编号。展示名在标题、正文和图片 Agent 之间
                        # 必须保持一致，因此在选定唯一主图后只识别一次，再统一绑定。
                        from app.services.erp_product_naming_service import (
                            enrich_erp_product_display_name,
                        )

                        product_name = await enrich_erp_product_display_name(
                            product_name=prepared_image.product.name,
                            image_url=prepared_image.reference_url,
                        )
                        print(
                            f"  🖼️ ERP 配图: {erp_image_config.commodity_category or '全部分类'}，"
                            f"已选择产品“{product_name}”作为图生图参考，"
                            f"近 {erp_image_config.repeat_after_days} 天不重复"
                        )

                    article_context = ""
                    image_context = ""
                    if task.knowledge_base_ids:
                        from app.database import PgSessionLocal

                        pg_db = PgSessionLocal()
                        try:
                            full_knowledge_context = load_required_knowledge_context(
                                db=pg_db,
                                knowledge_base_ids=task.knowledge_base_ids,
                                tenant_id=task.tenant_id,
                            )
                        finally:
                            pg_db.close()
                        # 知识库中的文章格式与产品背景属于不同 Agent 的输入。
                        # 在这里一次拆分，避免正文和图片生成阶段各自截断或重复
                        # 解析同一份完整资料，既节省 token，也保证职责边界一致。
                        prompt_contexts = split_knowledge_prompt_context(full_knowledge_context)
                        article_context = prompt_contexts.article_context
                        image_context = prompt_contexts.image_context
                        print(
                            f"  📚 已加载知识库: {task.knowledge_base_ids}，"
                            f"文章规则 {len(article_context)} 字符，"
                            f"图片背景规则 {len(image_context)} 字符"
                        )
                    elif erp_image_config:
                        raise ScheduledKnowledgeContextError(
                            "ERP 定时文章必须绑定知识库，任务已停止发布"
                        )

                    if product_name:
                        bind_product_context(
                            state=s,
                            product_name=product_name,
                            configured_topic=topic,
                            article_context=article_context,
                            image_context=image_context,
                            # 投喂源已定义本篇的文章结构，只由知识库约束 ERP
                            # 产品图的场景与背景；非投喂源模式仍要求完整格式规则。
                            require_article_context=not has_feed_source,
                        )
                    elif article_context or image_context:
                        # 非 ERP 的旧任务也按相同边界注入，避免投喂源图文仿写
                        # 在图片提示词里重复消耗文章版式规则。
                        s.kb_context = article_context
                        s.image_prompt_context = image_context

                await _prepare_product_and_knowledge_context()

                # 纯海报格式由知识库全文决定，不能沿用投喂源/普通文章的“标题、
                # 大纲、正文、配图”四步链路。格式配置会完整保留图片文案与页脚，
                # 再把同一张 ERP 原图传给每张海报，确保产品主体始终一致。
                if task.knowledge_base_ids:
                    from app.database import PgSessionLocal
                    from app.services.image_generation_service import image_generation_service
                    from app.services.poster_article_service import (
                        generate_poster_images,
                        generate_poster_plan,
                    )
                    from app.services.publication_format_service import (
                        load_publication_format_from_knowledge_bases,
                        render_poster_gallery_html,
                    )

                    pg_db = PgSessionLocal()
                    try:
                        publication_profile = load_publication_format_from_knowledge_bases(
                            db=pg_db,
                            knowledge_base_ids=task.knowledge_base_ids,
                            tenant_id=task.tenant_id,
                        )
                    finally:
                        pg_db.close()

                    if publication_profile.is_poster_gallery:
                        # 产品名在准备阶段已写入 ``ArticleState``，海报分支不能
                        # 引用准备函数的局部变量，否则异步边界外会出现未定义错误。
                        if not s.product_name:
                            raise ScheduledKnowledgeContextError(
                                "纯海报定时任务必须配置 ERP 产品图片来源"
                            )
                        print(
                            f"  🧩 发布格式: 纯海报拼接，标题海报 + "
                            f"{publication_profile.poster_count} 张内容海报"
                        )
                        s.footer_template = publication_profile.footer_template
                        poster_plan = await generate_poster_plan(
                            profile=publication_profile,
                            product_name=s.product_name,
                        )
                        poster_urls = await generate_poster_images(
                            profile=publication_profile,
                            plan=poster_plan,
                            product_name=s.product_name,
                            tenant_id=s.tenant_id,
                            reference_image_bytes=s.reference_image_bytes,
                            reference_content_type=s.reference_content_type,
                            generate_image=image_generation_service.generate,
                        )
                        s.title = SelectedTitle(
                            main_title=poster_plan.article_title,
                            sub_title="",
                        )
                        s.images = [
                            ImageResult(
                                position=index,
                                url=image_url,
                                method="poster_gallery",
                                keywords=poster_plan.posters[index - 1].copy,
                                section_title=poster_plan.posters[index - 1].scene,
                            )
                            for index, image_url in enumerate(poster_urls, start=1)
                        ]
                        s.content = render_poster_gallery_html(
                            image_urls=poster_urls,
                            footer_template=publication_profile.footer_template,
                        )
                        s.full_content = s.content
                        return s

                # Agent 1: 标题 — 返回 ArticleState
                s = await agent1_generate_title_options(s)
                if s.error:
                    raise RuntimeError(s.error)
                selected_title = (
                    s.title_options[0]
                    if s.title_options
                    else SelectedTitle(
                        main_title=s.topic or (s.reference_articles[0] if s.reference_articles else ""),
                        sub_title="",
                    )
                )
                s.title = (
                    ensure_product_name_in_title(selected_title, s.product_name)
                    if s.product_name else selected_title
                )

                # HTML 仿写已经锁定真实 DOM 槽位、顺序与目标长度。独立大纲既不
                # 改变槽位，也不会作为最终内容落库，只会额外产生一次文生文调用。
                # 因此该模式直接进入槽位内容 Agent；普通 Markdown/知识库任务仍
                # 保留大纲步骤，保证自由文章的结构完整性。
                if not s.reference_html:
                    s = await agent2_generate_outline(s)
                    if s.error:
                        raise RuntimeError(s.error)

                # Agent 3: 正文或 HTML 槽位内容
                s = await agent3_generate_content(s)
                if s.error:
                    raise RuntimeError(s.error)

                # 配图: 有参考图片时走理解+AI生成，否则走 AI 生图
                # 注意: 有 layout_template 时，agent 3 已按模板生成 [IMAGE:] 占位符
                # agent4/5 直接解析并配图即可
                from app.services.image_generation_service import is_image_generation_configured

                if is_image_generation_configured():
                    if 'data-ai-image-slot=' in (s.content or "") and s.image_requirements:
                        # HTML 仿写已完成格式、文字和图片需求分析，直接按原 img 节点
                        # 生成并回填图片，避免旧的 Markdown 占位符路径打乱图文位置。
                        s = await agent5_generate_images(s)
                        merge_images_into_content(s)
                    elif s.layout_template and s.content_blocks:
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
                    elif image_route.mode == "reference_visual_imitation" and ref_image_urls:
                        # 路径②：未选择 ERP 的投喂源任务才允许理解和仿写参考图片。
                        await _gen_images_from_references(s, ref_image_urls)
                        merge_images_into_content(s)
                    else:
                        # 路径③：ERP 路径的 state 已带 ERP 产品原图字节和知识库
                        # 背景规则，Agent 5 会执行图生图；普通路径继续文生图。
                        s = await agent4_analyze_image_requirements(s)
                        s = await agent5_generate_images(s)
                        merge_images_into_content(s)
                else:
                    s.full_content = s.content or ""

                return s

            return asyncio.run(_run_with_cos_cleanup(_run, relay_service, relay_object_keys))

        state = _run_pipeline(state)

        # 最终发布前以 HTML 为唯一真相收口所有正文图片。不能只处理 state.images：
        # HTML 仿写模板、封面或重试链路可能存在额外 img 节点。固定页脚二维码由
        # 服务自动识别并跳过，其他任一图片归档失败都会中止发布，杜绝混用版本。
        from app.services.article_publication_polish_service import (
            append_ai_image_disclaimer,
            normalize_final_article_images_with_attribution,
        )

        normalized_images = asyncio.run(
            normalize_final_article_images_with_attribution(
                db,
                content=state.full_content or state.content or "",
                tenant_id=task.tenant_id,
                # ERP 任务使用已识别的产品名；非 ERP 图文以最终标题作为署名，
                # 保证所有正文图片都有可读的业务归属。
                product_name=state.product_name or (
                    state.title.main_title if state.title else state.topic
                ),
            )
        )
        # 让 state 中的图片元数据同步使用归档版本，后续封面选择不能回退到临时 URL。
        for image in state.images:
            image.url = normalized_images.url_mapping.get(image.url, image.url)
        final_content = normalized_images.content
        state.content = final_content
        state.full_content = append_ai_image_disclaimer(final_content)

        # 5. 更新 Article
        title_text = state.title.main_title if state.title else (topic or "")
        article.topic = state.topic
        article.main_title = title_text
        article.content = state.full_content or state.content or ""
        article.full_content = state.full_content or state.content or ""

        cover_image_url = _select_article_cover(state, article.full_content or "")
        if cover_image_url:
            article.cover_image = cover_image_url
            print(f"  🖼️ 封面: {cover_image_url[:60]}")

        # 内容生成完成不等于微信已接收。先落中间态，发布失败时文章会明确停留在
        # generated，而不会因为异常分支提交运行记录而被误标为 draft_saved。
        article.status = "generated"
        article.phase = "CONTENT_GENERATED"
        db.commit()

        print(f"  ✅ 内容生成完成: {title_text[:40]}")

        # 7. 发布到微信
        _publish_to_wechat(db, article, account_ids, publish_mode, task)
        _finalize_article_delivery(db, article, publish_mode)
        generated_article_id = article.id

    db.commit()
    print(f"\n  ✅ 图文任务完成")
    return generated_article_id


async def _gen_images_from_references(state, ref_image_urls):
    """使用参考图片理解 + AI 生成配图"""
    import re as _re
    from app.agent.nodes.image_understanding_node import understand_images
    from app.agent.nodes.prompt_crafting_node import craft_prompt
    from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
    from app.services.image_generation_models import ImageGenerationRequest
    from app.services.image_generation_service import image_generation_service
    from app.services.reference_image_imitation_service import build_reference_image_prompt
    from app.services.reference_media_analysis_service import analyze_reference_images
    from app.schemas.article import ImageResult

    if not ref_image_urls:
        print(f"  ⚠️ 无参考图片，跳过 AI 配图")
        return

    print(f"  ▶ 理解参考图片（{len(ref_image_urls)} 张）...")
    try:
        analysis = await asyncio.to_thread(
            analyze_reference_images,
            ref_image_urls,
            understand_images,
        )
    except Exception as e:
        print(f"  ⚠️ 图片理解失败: {e}")
        return

    visual_descs = [image.description for image in analysis.usable_images]
    if analysis.skipped_qrcode_count:
        print(f"  🚫 已跳过 {analysis.skipped_qrcode_count} 张二维码参考图")
    if not visual_descs:
        print(f"  ⚠️ 图片理解未返回描述")
        return

    # 从正文中提取 [IMAGE:] 占位符
    placeholders = list(_re.finditer(r'\[IMAGE:position=(\d+),keywords=([^,\]]+),type=([^\]]+)\]', state.content or ""))
    if not placeholders:
        print(f"  ⚠️ 正文中无 [IMAGE:] 占位符")
        return

    print(f"  ▶ AI 生成配图（{len(placeholders)} 张）...")

    async def _gen_all():
        results = []
        for idx, m in enumerate(placeholders):
            pos = int(m.group(1))
            orig_kw = m.group(2)
            img_type = m.group(3)

            desc_idx = idx % len(visual_descs)
            desc = visual_descs[desc_idx]
            prompt = build_reference_image_prompt(
                desc,
                orig_kw,
                craft_prompt,
                build_wanxiang_prompt,
            )
            if not prompt:
                print(f"    ⚠️ 图片 {idx+1} 未生成提示词")
                results.append(
                    ImageResult(
                        position=pos,
                        url="",
                        method="WANXIANG_IMITATE",
                        keywords=orig_kw,
                        section_title="",
                        description=str(desc),
                        placeholder_id=m.group(0),
                    )
                )
                continue

            print(f"    >>> 图片 {idx+1}/{len(placeholders)} (pos={pos}) <<<")
            generated = await image_generation_service.generate(ImageGenerationRequest(
                prompt=prompt,
                size="1024*1365",
                tenant_id=state.tenant_id,
            ))
            img_url = generated.url
            method = (
                f"{generated.provider}-fallback"
                if generated.fallback_used
                else generated.provider
            )
            results.append(ImageResult(position=pos, url=img_url or "", method=method, keywords=orig_kw, section_title="", description=str(desc), placeholder_id=m.group(0)))
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
    from functools import partial
    from app.services.asset_archive_service import save_image_to_asset_library
    from app.services.image_generation_service import image_generation_service
    from app.services.storage_service import storage_service
    from app.services.wechat_publisher import publish_article
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
            from app.services.reference_image_imitation_service import imitate_reference_images
            from app.services.reference_media_analysis_service import extract_markdown_image_urls

            ref_title = ref.title or ""
            ref_body = ref.body_markdown or ""
            image_urls_from_ref = extract_markdown_image_urls(ref_body)
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

            # 两个纯图片入口统一复用同一编排服务，二维码只跳过自身，其余图片仍按
            # 原始顺序仿写，避免定时任务与即时任务的处理结果不一致。
            imitation_result = asyncio.run(
                imitate_reference_images(
                    image_urls_from_ref,
                    new_title,
                    tenant_id=task.tenant_id,
                    understand_images_fn=understand_images,
                    craft_prompt_fn=craft_prompt,
                    fallback_prompt_fn=build_wanxiang_prompt,
                    # 仿写服务维持供应商无关的回调边界；partial 固定租户，确保中转站
                    # 返回的 Base64 图片进入正确租户的 MinIO 目录。
                    generate_image_fn=partial(
                        image_generation_service.generate_image,
                        tenant_id=task.tenant_id,
                    ),
                    archive_image_fn=lambda tenant_id, image_url, **kwargs: save_image_to_asset_library(
                        db,
                        tenant_id,
                        image_url,
                        **kwargs,
                    ),
                )
            )
            image_urls = list(imitation_result.generated_urls)
            if imitation_result.skipped_qrcode_count:
                print(f"  🚫 已跳过 {imitation_result.skipped_qrcode_count} 张二维码参考图")

            if not image_urls:
                if imitation_result.skipped_qrcode_count == len(image_urls_from_ref):
                    print(f"  ⚠️ 所有参考图片均为二维码，已跳过仿写")
                else:
                    print(f"  ❌ 所有非二维码图片生成失败")
                return

            from app.services.article_publication_polish_service import (
                append_ai_image_disclaimer,
                archive_image_urls_with_attribution,
            )

            image_urls = asyncio.run(
                archive_image_urls_with_attribution(
                    db,
                    image_urls,
                    tenant_id=task.tenant_id,
                    product_name=new_title,
                )
            )

            body_md = append_ai_image_disclaimer(
                "\n\n".join(f"![]({url})" for url in image_urls)
            )

            article = Article(
                task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
                tenant_id=task.tenant_id,
                main_title=new_title,
                content=body_md,
                full_content=body_md,
                cover_image=image_urls[0],
                status="generated",
                phase="CONTENT_GENERATED",
            )
            db.add(article)
            db.flush()
            db.commit()

            _publish_to_wechat(db, article, account_ids, publish_mode, task)
            _finalize_article_delivery(db, article, publish_mode)
            print(f"  ✅ 纯图片仿写完成: {len(image_urls)} 张图")
            return article.id

        except Exception as e:
            print(f"  ❌ 仿写流程失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    # === 无投喂源：通用图片生成（3 张） ===
    # 有主题用主题，无主题就不生成（不兜底任务名称）
    if not topic:
        print(f"  ⚠️ 无主题且无投喂源，跳过纯图片生成")
        return
    async def _run():
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
                img_url = await image_generation_service.generate_image(
                    prompts[i],
                    size="1024*1365",
                    tenant_id=task.tenant_id,
                )
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

    from app.services.article_publication_polish_service import (
        append_ai_image_disclaimer,
        archive_image_urls_with_attribution,
    )

    image_urls = asyncio.run(
        archive_image_urls_with_attribution(
            db,
            image_urls,
            tenant_id=task.tenant_id,
            product_name=topic,
        )
    )

    body_md = append_ai_image_disclaimer(
        "\n\n".join(f"![]({url})" for url in image_urls)
    )

    article = Article(
        task_id=f"sched_img_{task.id}_{uuid.uuid4().hex[:6]}",
        tenant_id=task.tenant_id,
        main_title=topic,
        content=body_md,
        full_content=body_md,
        cover_image=image_urls[0],
        status="generated",
        phase="CONTENT_GENERATED",
    )
    db.add(article)
    db.flush()
    db.commit()

    _publish_to_wechat(db, article, account_ids, publish_mode, task)
    _finalize_article_delivery(db, article, publish_mode)
    print(f"  ✅ 纯图片完成: {len(image_urls)} 张图")
    return article.id


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
            from app.services.image_generation_service import image_generation_service
            cover_prompt = f"{use_topic}，封面图，视觉冲击力，高清，适合做视频封面"
            _cover_img_url = await image_generation_service.generate_image(
                cover_prompt,
                size="720*1280",
                tenant_id=task.tenant_id,
            )
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
        status="generated",
        phase="CONTENT_GENERATED",
    )
    db.add(article)
    db.flush()
    db.commit()

    _publish_to_wechat(db, article, account_ids, publish_mode, task)
    _finalize_article_delivery(db, article, publish_mode)
    print(f"  ✅ 视频完成")
    return article.id


def _publish_to_wechat(db, article, account_ids, publish_mode, task):
    """逐个调用公众号发布接口，任一失败都携带账号 ID 向上抛出。

    本方法只负责外部交付，不修改文章最终状态；调用方必须在本方法完整返回后调用
    ``_finalize_article_delivery``。这种顺序保证数据库状态表达真实外部结果。
    """
    from app.services.wechat_publisher import publish_article

    if not account_ids:
        raise ValueError("定时任务未配置公众号，无法完成发布")

    for aid in account_ids:
        try:
            publish_article(db, article, aid, mode=publish_mode,
                            tenant_id=task.tenant_id, actor_id=task.created_by or 0)
            logger.info(
                "已%s到公众号 #%s",
                "直接发布" if publish_mode == "direct" else "保存草稿",
                aid,
            )
        except Exception as e:
            logger.error("发布到公众号 #%s 失败: %s", aid, e)
            raise RuntimeError(f"发布到公众号 #{aid} 失败: {e}") from e


def _finalize_article_delivery(db, article, publish_mode: str) -> None:
    """在微信交付真实成功后统一写入文章最终状态并提交事务。

    图文、纯图片和视频共享该收口点，避免各流程自行维护状态字符串而再次出现
    “尚未保存微信草稿却已标记成功”的时序错误。
    """
    if publish_mode == "direct":
        article.status = "published"
        article.phase = "PUBLISHED"
    else:
        article.status = "draft_saved"
        article.phase = "DRAFT_SAVED"
    db.commit()


def _load_layout_template(state, feed_article) -> None:
    """加载并净化投喂源版式模板，禁止联系方式章节进入正文 Agent。"""
    from app.schemas.article import LayoutTemplate
    from app.services.reference_contact_filter_service import (
        remove_contact_sections_from_layout_template,
    )

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
        state.layout_template = remove_contact_sections_from_layout_template(
            LayoutTemplate(**template_data)
        )
        section_count = len(state.layout_template.sections)
        print(f"  Template loaded: {section_count} sections, {state.layout_template.total_image_count} images")
    except Exception as exc:
        print(f"  Template parse failed: {exc}")


def _build_reference_article_for_imitation(title: str, markdown: str) -> str:
    """构建安全的投喂源文字上下文，彻底移除来源账号的末尾联系区。"""

    from app.services.reference_contact_filter_service import strip_reference_contact_markdown

    cleaned_markdown = strip_reference_contact_markdown(markdown)
    if not cleaned_markdown:
        return ""
    return f"## {title}\n\n{cleaned_markdown}"
