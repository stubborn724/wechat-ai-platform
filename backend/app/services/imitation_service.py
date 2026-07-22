"""多源仿写服务 — 仿写池管理、任务调度、融合生成工作流"""

import json
import logging
import random
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.agent.nodes.structure_analysis_node import analyze_articles_batch
from app.models.mysql_models import (
    FeedSource,
    FeedSourceArticle,
    ImitationPool,
    ImitationPoolSource,
    ImitationTask,
    ImitationTaskResult,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 仿写池管理
# ============================================================================


def create_pool(db: Session, tenant_id: int, name: str, description: str = "") -> ImitationPool:
    """创建仿写池"""
    pool = ImitationPool(tenant_id=tenant_id, name=name, description=description)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    logger.info("Created imitation pool: %s (id=%d)", name, pool.id)
    return pool


def list_pools(db: Session, tenant_id: int) -> List[ImitationPool]:
    """列出租户的所有启用的仿写池"""
    return (
        db.query(ImitationPool)
        .filter(
            ImitationPool.tenant_id == tenant_id,
            ImitationPool.is_active == True,
        )
        .order_by(ImitationPool.id.desc())
        .all()
    )


def add_source_to_pool(
    db: Session,
    pool_id: int,
    feed_source_id: Optional[int] = None,
    wechat_name: Optional[str] = None,
    wechat_app_id: Optional[str] = None,
    weight: int = 1,
) -> ImitationPoolSource:
    """向仿写池添加来源

    Args:
        pool_id: 仿写池 ID
        feed_source_id: FeedSource ID（优先）
        wechat_name: 公众号名称（直接录入）
        wechat_app_id: 公众号 AppID（直接录入）
        weight: 权重
    """
    source = ImitationPoolSource(
        pool_id=pool_id,
        feed_source_id=feed_source_id,
        wechat_name=wechat_name,
        wechat_app_id=wechat_app_id,
        weight=weight,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info("Added source to pool %d: feed_source=%s, wechat=%s", pool_id, feed_source_id, wechat_name)
    return source


def list_pool_sources(db: Session, pool_id: int) -> List[dict]:
    """列出仿写池来源（带 FeedSource 信息）"""
    sources = (
        db.query(ImitationPoolSource)
        .filter(
            ImitationPoolSource.pool_id == pool_id,
            ImitationPoolSource.is_active == True,
        )
        .all()
    )
    results = []
    for s in sources:
        item = {
            "id": s.id,
            "feed_source_id": s.feed_source_id,
            "wechat_name": s.wechat_name,
            "wechat_app_id": s.wechat_app_id,
            "weight": s.weight,
        }
        if s.feed_source_id:
            fs = db.query(FeedSource).filter(FeedSource.id == s.feed_source_id).first()
            if fs:
                item["source_name"] = fs.name
                item["source_type"] = fs.source_type
                # Count articles
                count = (
                    db.query(FeedSourceArticle)
                    .filter(FeedSourceArticle.feed_source_id == fs.id)
                    .count()
                )
                item["article_count"] = count
        results.append(item)
    return results


def remove_source_from_pool(db: Session, source_id: int) -> bool:
    """从仿写池移除来源"""
    source = db.query(ImitationPoolSource).filter(ImitationPoolSource.id == source_id).first()
    if not source:
        return False
    source.is_active = False
    db.commit()
    return True


# ============================================================================
# 结构分析 — 对仿写池中的来源进行批量分析
# ============================================================================


def analyze_pool_sources(db: Session, pool_id: int) -> List[dict]:
    """对仿写池中所有来源进行结构分析，结果回写到 FeedSource.style_profile"""
    sources = (
        db.query(ImitationPoolSource)
        .filter(
            ImitationPoolSource.pool_id == pool_id,
            ImitationPoolSource.is_active == True,
            ImitationPoolSource.feed_source_id.isnot(None),
        )
        .all()
    )

    results = []
    for s in sources:
        fs = db.query(FeedSource).filter(FeedSource.id == s.feed_source_id).first()
        if not fs:
            continue

        # 获取该来源最新的 5 篇文章
        articles = (
            db.query(FeedSourceArticle)
            .filter(
                FeedSourceArticle.feed_source_id == fs.id,
                FeedSourceArticle.body_markdown.isnot(None),
            )
            .order_by(FeedSourceArticle.id.desc())
            .limit(5)
            .all()
        )

        contents = [a.body_markdown for a in articles if a.body_markdown and len(a.body_markdown) > 200]
        if not contents:
            results.append({"source_id": fs.id, "source_name": fs.name, "status": "no_content"})
            continue

        analysis = analyze_articles_batch(contents)
        if analysis:
            # 将完整分析存入 style_profile
            profile = analysis.to_dict()
            # 合并原有的风格分析结果
            if fs.style_profile:
                if isinstance(fs.style_profile, str):
                    try:
                        old = json.loads(fs.style_profile)
                    except json.JSONDecodeError:
                        old = {}
                else:
                    old = fs.style_profile
                old["structure_analysis"] = profile
                fs.style_profile = old
            else:
                fs.style_profile = {"structure_analysis": profile}
            db.commit()
            results.append({
                "source_id": fs.id,
                "source_name": fs.name,
                "status": "analyzed",
                "score": profile.get("overall_score", 0),
            })
            logger.info("Analyzed pool source %s: score=%d", fs.name, profile.get("overall_score", 0))
        else:
            results.append({"source_id": fs.id, "source_name": fs.name, "status": "analysis_failed"})

    return results


# ============================================================================
# 仿写任务调度器
# ============================================================================


def create_imitation_task(
    db: Session,
    tenant_id: int,
    name: str,
    pool_id: int,
    strategy: str = "random",
    articles_per_day: int = 1,
    content_types: Optional[list] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    publish_times: Optional[list] = None,
    account_id: Optional[int] = None,
    approval_mode: str = "auto",
    knowledge_base_ids: Optional[list] = None,
    footer_template: Optional[str] = None,
    created_by: Optional[int] = None,
) -> ImitationTask:
    """创建仿写任务"""
    task = ImitationTask(
        tenant_id=tenant_id,
        pool_id=pool_id,
        name=name,
        strategy=strategy,
        articles_per_day=articles_per_day,
        content_types=content_types or ["article"],
        start_date=start_date,
        end_date=end_date,
        publish_times=publish_times,
        account_id=account_id,
        approval_mode=approval_mode,
        knowledge_base_ids=knowledge_base_ids,
        footer_template=footer_template,
        created_by=created_by,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("Created imitation task: %s (strategy=%s, %d/day)", name, strategy, articles_per_day)
    return task


def list_imitation_tasks(db: Session, tenant_id: int) -> List[ImitationTask]:
    """列出租户的仿写任务"""
    return (
        db.query(ImitationTask)
        .filter(ImitationTask.tenant_id == tenant_id)
        .order_by(ImitationTask.id.desc())
        .all()
    )


def select_sources_for_task(db: Session, task: ImitationTask, count: int) -> List[dict]:
    """根据策略选择本次要仿写的来源

    Returns:
        [{"source_id": ..., "source_name": ..., "feed_source_id": ..., "articles": [...]}, ...]
    """
    sources = (
        db.query(ImitationPoolSource)
        .filter(
            ImitationPoolSource.pool_id == task.pool_id,
            ImitationPoolSource.is_active == True,
        )
        .all()
    )

    if not sources:
        return []

    selected = []
    if task.strategy == "random":
        # 带权随机
        weights = [s.weight for s in sources]
        chosen = random.choices(sources, weights=weights, k=min(count, len(sources)))
        selected = list(set(chosen))  # 去重
    elif task.strategy == "round_robin":
        # 轮流：按 ID 排序取
        sources.sort(key=lambda s: s.id)
        selected = sources[:count]
    elif task.strategy == "exhaust":
        # 全部仿写完 — 优先选还没有仿写过或仿写次数最少的
        # 简化：取所有
        selected = sources

    # 限制数量
    selected = selected[:count]

    # 获取每篇文章
    result = []
    for s in selected:
        source_info = {"source_id": s.id, "feed_source_id": s.feed_source_id, "articles": []}

        if s.feed_source_id:
            fs = db.query(FeedSource).filter(FeedSource.id == s.feed_source_id).first()
            source_info["source_name"] = fs.name if fs else s.wechat_name or "unknown"

            # 获取最新的文章用于仿写
            articles = (
                db.query(FeedSourceArticle)
                .filter(
                    FeedSourceArticle.feed_source_id == s.feed_source_id,
                    FeedSourceArticle.body_markdown.isnot(None),
                )
                .order_by(FeedSourceArticle.id.desc())
                .limit(3)
                .all()
            )
            source_info["articles"] = [
                {
                    "id": a.id,
                    "title": a.title,
                    "body_markdown": a.body_markdown,
                    "summary": a.summary,
                }
                for a in articles
            ]
        else:
            source_info["source_name"] = s.wechat_name or "unknown"

        result.append(source_info)

    return result


# ============================================================================
# 融合生成 — 执行一篇仿写生成
# ============================================================================


async def execute_imitation_generation(
    db: Session,
    task: ImitationTask,
    source_info: dict,
    slot_index: int,
) -> dict:
    """执行一篇仿写文章的完整生成

    融合工作流:
      目标文章结构分析 → 我方知识库内容 → 我方素材 → 底部模板 → LangGraph 生成
    """
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_agent_service import (
        agent1_generate_title_options,
        agent2_generate_outline,
        agent3_generate_content,
        agent4_analyze_image_requirements,
        agent5_generate_images,
        merge_images_into_content,
    )

    topic = task.name
    # 取第一篇文章作为仿写参考
    ref_articles = source_info.get("articles", [])
    ref_content = ref_articles[0].get("body_markdown", "") if ref_articles else ""

    # 1. 结构分析
    structure_analysis = None
    if ref_content:
        from app.agent.nodes.structure_analysis_node import analyze_article_structure
        structure_analysis = analyze_article_structure(ref_content)

    # 2. 构建知识库上下文
    kb_context = ""
    if task.knowledge_base_ids:
        from app.database import get_pg_db
        from app.services.knowledge_base_service import search_all_knowledge_bases

        try:
            pg_db = next(get_pg_db())
            all_chunks = []
            for kb_id in task.knowledge_base_ids:
                from app.services.knowledge_base_service import search_knowledge_base
                results = search_knowledge_base(pg_db, kb_id, topic, top_k=3)
                all_chunks.extend(results)

            if all_chunks:
                context_parts = []
                for r in all_chunks:
                    context_parts.append(
                        f"[来源: 知识库 chunk_id={r['id']} 相似度={r['score']:.2f}]\n{r['content']}"
                    )
                kb_context = "\n\n---\n\n".join(context_parts)
            pg_db.close()
        except Exception as exc:
            logger.warning("KB search failed: %s", exc)

    # 3. 构建仿写引导 prompt
    imitation_guide = ""
    if ref_content:
        imitation_guide = "\n\n## 📝 仿写参考文章\n请参考以下文章的写作风格和结构进行仿写，但内容要使用我方知识库的信息。\n\n"
        if ref_articles and ref_articles[0].get("title"):
            imitation_guide += f"参考标题: {ref_articles[0]['title']}\n\n"
        imitation_guide += f"参考正文:\n{ref_content[:3000]}\n"

    if structure_analysis:
        imitation_guide += f"\n{structure_analysis.to_prompt_section()}\n"

    if kb_context:
        imitation_guide += f"\n\n## 📚 知识库参考资料（用这些信息填充内容）\n{kb_context}\n"

    # 4. 执行生成
    state = ArticleState(
        task_id=f"imitation_{task.id}_{slot_index}_{datetime.now(timezone.utc).timestamp()}",
        user_id=task.created_by or 0,
        topic=topic,
        style="default",
        footer_template=task.footer_template,
        kb_context=kb_context or None,
    )

    # 注入仿写引导（转义花括号防止 .format() 崩溃）
    enriched_topic = f"{topic}\n{imitation_guide}" if imitation_guide else topic
    enriched_topic = enriched_topic.replace("{", "{{").replace("}", "}}")
    state.topic = enriched_topic

    try:
        # Step 1: 标题
        state = await agent1_generate_title_options(state)
        if not state.title_options:
            raise Exception("Title generation failed")
        first = state.title_options[0]
        state.title = SelectedTitle(main_title=first.main_title, sub_title=first.sub_title)

        # Step 2: 大纲
        state = await agent2_generate_outline(state)

        # Step 3: 正文
        state = await agent3_generate_content(state)

        # Step 4: 配图
        state = await agent4_analyze_image_requirements(state)
        state = await agent5_generate_images(state)
        state = merge_images_into_content(state)

        return {
            "success": True,
            "title": f"{first.main_title} - {first.sub_title}",
            "body_markdown": state.full_content or state.content or "",
            "summary": first.sub_title,
            "images": [img.url for img in state.images if img.url],
            "structure_analysis": structure_analysis.to_dict() if structure_analysis else None,
        }
    except Exception as exc:
        logger.error("Imitation generation slot %d failed: %s", slot_index, exc)
        return {
            "success": False,
            "error": str(exc),
            "title": topic,
            "body_markdown": "",
        }


# ============================================================================
# 执行一次仿写任务（一天的量）
# ============================================================================


async def execute_imitation_task(db: Session, task_id: int) -> dict:
    """执行一个仿写任务 — 按配置生成 articles_per_day 篇文章"""
    task = db.query(ImitationTask).filter(ImitationTask.id == task_id).first()
    if not task:
        return {"error": f"Task {task_id} not found"}

    if task.status != "active":
        return {"error": f"Task {task_id} is not active (status={task.status})"}

    count = task.articles_per_day
    sources = select_sources_for_task(db, task, count)

    if not sources:
        return {"error": "No sources available in pool"}

    results = []
    for i, source in enumerate(sources):
        result = await execute_imitation_generation(db, task, source, i)

        # 保存结果
        task_result = ImitationTaskResult(
            tenant_id=task.tenant_id,
            task_id=task.id,
            pool_source_id=source["source_id"],
            source_name=source.get("source_name", ""),
            structure_analysis=result.get("structure_analysis"),
            status="generated" if result["success"] else "failed",
            error_message=result.get("error", "") if not result["success"] else None,
        )
        db.add(task_result)
        results.append(result)

    # 更新任务统计
    task.total_generated = (task.total_generated or 0) + sum(1 for r in results if r["success"])
    db.commit()

    return {
        "task_id": task.id,
        "task_name": task.name,
        "generated": sum(1 for r in results if r["success"]),
        "failed": sum(1 for r in results if not r["success"]),
        "results": results,
    }
