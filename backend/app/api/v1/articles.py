"""Article generation routes (from ai-passage-creator)"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.config import settings
from app.models.mysql_models import AgentLog, Article

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class CreateArticleRequest(BaseModel):
    topic: str
    style: Optional[str] = None
    image_source: str = "PEXELS"
    enabled_image_methods: Optional[List[str]] = None
    user_description: Optional[str] = None
    mode: str = "manual"  # "manual" or "auto"
    article_count: int = 1
    account_id: Optional[int] = None  # auto 模式下自动保存草稿
    knowledge_base_ids: Optional[List[int]] = None  # 知识库ID列表，用于注入参考内容
    source_feed_id: Optional[int] = None  # Feed源ID，用于仿写模式
    feed_article_ids: Optional[List[int]] = None  # 具体要仿写的文章ID列表
    selected_image_urls: Optional[List[str]] = None  # 本地素材预选图片URL
    footer_template: Optional[str] = None  # 文章底部固定内容


class ConfirmTitleRequest(BaseModel):
    main_title: str
    sub_title: str
    user_description: Optional[str] = None


class OutlineSectionSchema(BaseModel):
    section: int
    title: str
    points: List[str]


class OutlineResultSchema(BaseModel):
    sections: List[OutlineSectionSchema]


class ConfirmOutlineRequest(BaseModel):
    outline: OutlineResultSchema


class AiModifyOutlineRequest(BaseModel):
    instruction: str
    outline: OutlineResultSchema


class ArticleResponse(BaseModel):
    id: int
    task_id: str
    user_id: Optional[int] = None
    topic: Optional[str] = None
    style: Optional[str] = None
    main_title: Optional[str] = None
    sub_title: Optional[str] = None
    title_options: Optional[list] = None
    outline: Optional[dict] = None
    content: Optional[str] = None
    full_content: Optional[str] = None
    cover_image: Optional[str] = None
    images: Optional[list] = None
    status: str
    phase: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArticleListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ArticleResponse]


class AgentLogResponse(BaseModel):
    id: int
    task_id: str
    agent_name: str
    status: str
    prompt: Optional[str] = None
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    error_message: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_io(agent_name: str, prompt: str, response: str, duration_ms: int):
    """Print a coloured log block so the user can trace model I/O in the
    uvicorn console."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  🤖 [{agent_name}]")
    print(f"  ⏱  {duration_ms} ms")
    print(f"{sep}")
    print(f"  📥 PROMPT 输入:")
    print(f"{prompt}")
    print(f"{sep}")
    print(f"  📤 RESPONSE 输出:")
    print(f"{response}")
    print(f"{sep}\n")


def _require_api_key():
    """Raise if no dashscope_api_key is configured."""
    if not settings.dashscope_api_key:
        raise RuntimeError(
            "dashscope_api_key 未配置。请在 .env 中设置 DASHSCOPE_API_KEY=your-key"
        )


# ---------------------------------------------------------------------------
# Agent runners (sync wrappers around async agent functions)
# ---------------------------------------------------------------------------

async def _run_title_agent(state):
    """Run agent1 (title generation). Returns list of title option dicts."""
    from app.services.article_agent_service import (
        AGENT1_TITLE_PROMPT,
        agent1_generate_title_options,
    )

    _require_api_key()
    t0 = time.perf_counter()
    state = await agent1_generate_title_options(state)
    elapsed = int((time.perf_counter() - t0) * 1000)

    prompt = AGENT1_TITLE_PROMPT.format(topic=state.topic, style=state.style or "default")
    raw = json.dumps([opt.model_dump() for opt in state.title_options], ensure_ascii=False)
    _log_io("Agent1 标题生成", prompt, raw, elapsed)
    return [opt.model_dump() for opt in state.title_options]


async def _run_outline_agent(state):
    """Run agent2 (outline generation). Returns outline dict."""
    from app.services.article_agent_service import AGENT2_OUTLINE_PROMPT, agent2_generate_outline

    _require_api_key()
    t0 = time.perf_counter()
    state = await agent2_generate_outline(state)
    elapsed = int((time.perf_counter() - t0) * 1000)

    prompt = AGENT2_OUTLINE_PROMPT.format(
        topic=state.topic, main_title=state.title.main_title,
        sub_title=state.title.sub_title, style=state.style or "default",
        user_description=state.user_description or "无", style_section="",
    )
    raw = json.dumps(state.outline.model_dump() if state.outline else {}, ensure_ascii=False)
    _log_io("Agent2 大纲生成", prompt, raw, elapsed)
    return state.outline.model_dump() if state.outline else {}


async def _run_content_agent(state):
    """Run agent3 (content generation). Returns full content string."""
    from app.services.article_agent_service import (
        AGENT3_CONTENT_PROMPT, _build_outline_text, agent3_generate_content,
    )

    _require_api_key()
    t0 = time.perf_counter()
    state = await agent3_generate_content(state)
    elapsed = int((time.perf_counter() - t0) * 1000)

    outline_text = _build_outline_text(state)
    prompt = AGENT3_CONTENT_PROMPT.format(
        main_title=state.title.main_title, sub_title=state.title.sub_title,
        style=state.style or "default", outline_text=outline_text, style_section="",
    )
    raw = state.content or ""
    _log_io("Agent3 正文生成", prompt, raw, elapsed)
    return state.content or ""


# ---------------------------------------------------------------------------
# Fallback sample data (when no API key)
# ---------------------------------------------------------------------------

def _sample_outline(topic: str, main_title: str) -> dict:
    return {
        "sections": [
            {"section": 1, "title": f"引言：{topic}的背景", "points": [f"{topic}的现状与挑战", "为什么这个话题值得关注"]},
            {"section": 2, "title": f"{topic}的核心内容", "points": ["关键概念解析", "主要观点与论据", "实际案例分析"]},
            {"section": 3, "title": f"{topic}的影响与意义", "points": ["对行业的影响", "对个人的启示", "未来发展趋势"]},
            {"section": 4, "title": "总结与展望", "points": ["核心要点回顾", "行动建议", "延伸思考"]},
        ]
    }


def _sample_content(main_title: str, sub_title: str, outline: dict) -> str:
    sections = outline.get("sections", [])
    lines = [f"# {main_title}\n", f"*{sub_title}*\n"]
    for sec in sections:
        lines.append(f"\n## {sec.get('title', '')}\n")
        for point in sec.get("points", []):
            lines.append(f"{point}。这是由示例数据生成的占位内容。在实际部署中，AI 将根据您选择的风格和主题生成高质量的原创文章。\n")
    lines.append("\n---\n*本文由 AI 运营平台生成*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/articles/create", status_code=status.HTTP_201_CREATED)
async def create_article(
    req: CreateArticleRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create an article and kick off title generation."""
    from app.services.article_service import create_article as service_create

    article = service_create(
        db=db, user_id=principal.user_id, topic=req.topic,
        style=req.style or "", image_source=req.image_source,
        footer_template=req.footer_template,
    )
    print(f"\n{'='*60}")
    print(f"  📝 创建文章: task_id={article.task_id}")
    print(f"  主题: {req.topic}  风格: {req.style or 'default'}")
    print(f"{'='*60}")

    try:
        from app.schemas.article import ArticleState
        from app.services.article_service import save_title_options

        state = ArticleState(
            task_id=article.task_id, user_id=principal.user_id,
            topic=req.topic, style=req.style or "default",
            enabled_image_methods=req.enabled_image_methods or ["PEXELS", "DASHSCOPE"],
            knowledge_base_ids=req.knowledge_base_ids,
            source_feed_id=req.source_feed_id,
            feed_article_ids=req.feed_article_ids,
            footer_template=req.footer_template,
            selected_image_urls=req.selected_image_urls,
        )

        # Retrieve knowledge base context if KB IDs provided
        if req.knowledge_base_ids:
            try:
                from app.database import get_pg_db
                from app.services.knowledge_base_service import search_knowledge_base

                pg_db = next(get_pg_db())
                try:
                    all_chunks = []
                    for kb_id in req.knowledge_base_ids:
                        results = search_knowledge_base(pg_db, kb_id, req.topic, top_k=3)
                        all_chunks.extend(results)

                    if all_chunks:
                        context_parts = []
                        for r in all_chunks:
                            context_parts.append(
                                f"[来源: 知识库 chunk_id={r['id']} 相似度={r['score']:.2f}]\n{r['content']}"
                            )
                        state.kb_context = "\n\n---\n\n".join(context_parts)
                        print(f"  📚 已加载知识库上下文: {len(all_chunks)} 个相关片段")
                finally:
                    pg_db.close()
            except Exception as exc:
                print(f"  ⚠️ 知识库检索失败: {exc}")

        # ========== 加载投喂源文章内容（仿写用） ==========
        if req.source_feed_id and req.feed_article_ids:
            try:
                from app.models.mysql_models import FeedSourceArticle
                articles_to_imitate = (
                    db.query(FeedSourceArticle)
                    .filter(
                        FeedSourceArticle.id.in_(req.feed_article_ids),
                        FeedSourceArticle.feed_source_id == req.source_feed_id,
                    )
                    .all()
                )
                if articles_to_imitate:
                    ref_texts = []
                    for a in articles_to_imitate:
                        title = a.title or ""
                        body = a.body_markdown or ""
                        ref_texts.append(f"## 参考文章：{title}\n\n{body}")
                    state.reference_articles = ref_texts
                    print(f"  📄 已加载 {len(ref_texts)} 篇参考文章用于仿写")
                    for a in articles_to_imitate:
                        print(f"     - {a.title}")
                else:
                    print(f"  ⚠️ 未找到指定的仿写文章")
            except Exception as exc:
                print(f"  ⚠️ 加载参考文章失败: {exc}")

        # Load style profile from feed source for imitation mode
        if req.source_feed_id:
            try:
                from app.models.mysql_models import FeedSource
                feed_src = db.query(FeedSource).filter(
                    FeedSource.id == req.source_feed_id
                ).first()
                if feed_src and feed_src.style_profile:
                    state.style_profile = feed_src.style_profile
                    print(f"  🎯 已加载仿写风格: {feed_src.name}")
                    print(f"     语气={feed_src.style_profile.get('tone', 'N/A')}, "
                          f"词汇={feed_src.style_profile.get('vocabulary_level', 'N/A')}")
                elif feed_src:
                    print(f"  ⚠️ 投喂源 '{feed_src.name}' 还没有风格分析结果，请先执行「分析」")
                else:
                    print(f"  ⚠️ 未找到投喂源 id={req.source_feed_id}")
            except Exception as exc:
                print(f"  ⚠️ 加载投喂源风格失败: {exc}")

        # ========== AUTO MODE: 跳过标题生成，直接用主题作为标题 ==========
        if req.mode == "auto":
            from app.schemas.article import SelectedTitle
            from app.services.article_service import save_outline, save_content

            # 直接用主题作为标题
            main_title = req.topic
            sub_title = ""
            state.title = SelectedTitle(main_title=main_title, sub_title=sub_title)
            article.main_title = main_title
            article.sub_title = sub_title
            article.title_options = [{"main_title": main_title, "sub_title": sub_title}]
            db.commit()
            print(f"  ▶ [自动] 直接用主题作为标题: {main_title}")

            # Generate outline
            if settings.dashscope_api_key:
                outline_data = await _run_outline_agent(state)
            else:
                outline_data = _sample_outline(req.topic, main_title)
            save_outline(db, article.task_id, outline_data)
            print(f"  ▶ [自动] 大纲已生成")

            # Generate content
            if settings.dashscope_api_key:
                content = await _run_content_agent(state)
                state.content = content
            else:
                content = _sample_content(main_title, sub_title, outline_data)
            print(f"  ▶ [自动] 正文已生成 ({len(content)} chars)")

            # ===== Generate images (Agent4+Agent5) & auto-archive =====
            cover_image_url = None
            if settings.dashscope_api_key:
                try:
                    from app.services.article_agent_service import (
                        agent4_analyze_image_requirements,
                        agent5_generate_images,
                        merge_images_into_content,
                    )
                    # Step 1: Generate a dedicated AI cover image from the article title
                    print("  ▶ [自动] 生成AI封面图...")
                    try:
                        from app.services.wanxiang_service import WanxiangImageService
                        cover_prompt = f"公众号文章封面图：{main_title}。扁平化设计，简洁大气，适合社交媒体传播。"
                        ws = WanxiangImageService()
                        cover_url = await ws.generate_image(cover_prompt, size="1024*1024")
                        if cover_url:
                            cover_image_url = cover_url
                            print(f"  ✅ AI封面图生成成功: {cover_url[:60]}")
                        else:
                            print(f"  ⚠️ AI封面生成失败，将使用正文配图")
                    except Exception as cover_err:
                        print(f"  ⚠️ AI封面图生成异常: {cover_err}")

                    print("  ▶ [自动] 分析配图需求...")
                    state = await agent4_analyze_image_requirements(state)
                    print(f"     需要 {len(state.image_requirements)} 张配图")

                    print("  ▶ [自动] 获取配图...")
                    state = await agent5_generate_images(state)
                    print(f"     已获取 {len(state.images)} 张配图")

                    # Merge images into content
                    state = merge_images_into_content(state)
                    content_rich = state.full_content or content

                    # If we have an AI cover, prepend it to the content
                    if cover_image_url:
                        content_rich = f"![封面]({cover_image_url})\n\n{content_rich}"

                    # Save images to asset library
                    if state.images:
                        from app.services.asset_archive_service import save_images_to_asset_library
                        image_urls = [img.url for img in state.images if img.url]
                        print(f"  ▶ [自动] 归档 {len(image_urls)} 张素材到素材库...")
                        await save_images_to_asset_library(db, principal.tenant_id, image_urls)
                        print(f"  ✅ 素材归档完成")

                except Exception as img_exc:
                    print(f"  ⚠️ 配图处理失败，使用占位图: {img_exc}")
                    import re as _re
                    def _ph(m):
                        pos = _re.search(r'position=(\d+)', m.group(1))
                        idx = int(pos.group(1)) if pos else 1
                        return f'<img src="https://picsum.photos/seed/{article.task_id[:8]}{idx}/800/400" style="width:100%;border-radius:8px;margin:16px 0;" />'
                    content_rich = _re.sub(r'\[IMAGE:(.*?)\]', _ph, content)
            else:
                import re as _re
                def _ph(m):
                    pos = _re.search(r'position=(\d+)', m.group(1))
                    idx = int(pos.group(1)) if pos else 1
                    return f'<img src="https://picsum.photos/seed/{article.task_id[:8]}{idx}/800/400" style="width:100%;border-radius:8px;margin:16px 0;" />'
                content_rich = _re.sub(r'\[IMAGE:(.*?)\]', _ph, content)

            # Footer is already appended by merge_images_into_content() via state.footer_template

            save_content(db, article.task_id, content, content_rich,
                         cover_image=cover_image_url,
                         footer_template=req.footer_template)
            if cover_image_url:
                article.cover_image = cover_image_url
            article.status = "completed"
            article.phase = "ALL_COMPLETE"
            db.commit()
            print(f"  ✅ [自动] 全流程完成！")

            # 自动保存到微信公众号草稿
            if req.account_id:
                from app.services.wechat_publisher import save_article_as_draft
                try:
                    draft_result = save_article_as_draft(db, article, req.account_id)
                    media_id = draft_result.get("media_id")
                    print(f"  ✅ [自动] 已保存到微信草稿箱! media_id={media_id}")
                    article.status = "published"
                    article.phase = "PUBLISHED"
                    db.commit()
                except Exception as draft_err:
                    print(f"  ⚠️ [自动] 保存微信草稿失败: {draft_err}")

        # ========== MANUAL MODE: 生成标题供用户选择 ==========
        else:
            if settings.dashscope_api_key:
                print("  ▶ 运行 Agent1 标题生成...")
                title_opts = await _run_title_agent(state)
            else:
                print("  ▶ 使用示例标题 (dashscope_api_key 未配置)")
                title_opts = [
                    {"main_title": f"{req.topic}：深度解析与未来展望", "sub_title": "一文读懂核心要点"},
                    {"main_title": f"揭秘{req.topic}背后的真相", "sub_title": "你可能不知道的五个事实"},
                    {"main_title": f"{req.topic}实用指南", "sub_title": "从入门到精通"},
                    {"main_title": f"为什么{req.topic}如此重要", "sub_title": "影响你我的关键因素"},
                    {"main_title": f"{req.topic}的过去、现在与未来", "sub_title": "全面回顾与发展趋势"},
                ]

            if title_opts:
                save_title_options(db, article.task_id, title_opts)
                print(f"  ✅ 已保存 {len(title_opts)} 个标题方案\n")

    except Exception as exc:
        logger.warning("Article generation pipeline failed: %s", exc)
        print(f"  ❌ 生成失败: {exc}\n")
        import traceback
        traceback.print_exc()
        db.refresh(article)

    return article


@router.post("/articles/{task_id}/confirm-title")
async def confirm_title(
    task_id: str,
    req: ConfirmTitleRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Confirm the selected title, then generate outline (agent2)."""
    article = db.query(Article).filter(Article.task_id == task_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    article.main_title = req.main_title
    article.sub_title = req.sub_title
    db.commit()
    print(f"\n{'='*60}")
    print(f"  ✅ 标题已确认: {req.main_title} / {req.sub_title}")
    print(f"{'='*60}")

    try:
        from app.schemas.article import ArticleState, SelectedTitle
        from app.services.article_service import save_outline

        state = ArticleState(
            task_id=task_id, user_id=principal.user_id,
            topic=article.topic or "", style=article.style or "default",
            title=SelectedTitle(main_title=req.main_title, sub_title=req.sub_title),
            user_description=req.user_description or article.topic,
        )

        if settings.dashscope_api_key:
            print("  ▶ 运行 Agent2 大纲生成...")
            outline_data = await _run_outline_agent(state)
        else:
            print("  ▶ 使用示例大纲 (dashscope_api_key 未配置)")
            outline_data = _sample_outline(article.topic or "", req.main_title)

        save_outline(db, task_id, outline_data)
        print(f"  ✅ 大纲已保存\n")

    except Exception as exc:
        logger.warning("Outline generation failed: %s", exc)
        print(f"  ❌ 大纲生成失败: {exc}\n")
        import traceback
        traceback.print_exc()

    return {"message": "Title confirmed", "task_id": task_id}


@router.post("/articles/{task_id}/confirm-outline")
async def confirm_outline(
    task_id: str,
    req: ConfirmOutlineRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Confirm the outline, then generate content (agent3)."""
    article = db.query(Article).filter(Article.task_id == task_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    outline_data = req.outline.model_dump()
    article.outline = outline_data
    db.commit()
    print(f"\n{'='*60}")
    print(f"  ✅ 大纲已确认")
    print(f"{'='*60}")

    try:
        from app.schemas.article import ArticleState, OutlineResult, SelectedTitle
        from app.services.article_service import save_content

        outline_result = OutlineResult(**outline_data)
        state = ArticleState(
            task_id=task_id, user_id=principal.user_id,
            topic=article.topic or "", style=article.style or "default",
            title=SelectedTitle(main_title=article.main_title or "", sub_title=article.sub_title or ""),
            outline=outline_result,
        )

        if settings.dashscope_api_key:
            print("  ▶ 运行 Agent3 正文生成...")
            content = await _run_content_agent(state)
        else:
            print("  ▶ 使用示例正文 (dashscope_api_key 未配置)")
            content = _sample_content(article.main_title or "", article.sub_title or "", outline_data)

        # Generate a dedicated AI cover image from the title
        cover_image_url = None
        if settings.dashscope_api_key:
            try:
                from app.services.wanxiang_service import WanxiangImageService
                cover_prompt = f"公众号文章封面图：{article.main_title or article.topic}。扁平化设计，简洁大气。"
                ws = WanxiangImageService()
                cover_url = await ws.generate_image(cover_prompt, size="1024*1024")
                if cover_url:
                    cover_image_url = cover_url
                    print(f"  ✅ AI封面图生成成功: {cover_url[:60]}")
            except Exception as cover_err:
                print(f"  ⚠️ AI封面生成失败: {cover_err}")

        # Generate images via Agent4+Agent5
        if settings.dashscope_api_key:
            try:
                from app.services.article_agent_service import (
                    agent4_analyze_image_requirements,
                    agent5_generate_images,
                    merge_images_into_content,
                )
                state.content = content
                state.footer_template = article.footer_template

                print("  ▶ 分析配图需求...")
                state = await agent4_analyze_image_requirements(state)
                print(f"     需要 {len(state.image_requirements)} 张配图")

                print("  ▶ 获取配图...")
                state = await agent5_generate_images(state)
                print(f"     已获取 {len(state.images)} 张配图")

                # Merge images into content (also appends footer)
                state = merge_images_into_content(state)
                full_content = state.full_content or content

                # Prepend AI cover if available
                if cover_image_url:
                    full_content = f"![封面]({cover_image_url})\n\n{full_content}"

                # Save to asset library
                if state.images:
                    from app.services.asset_archive_service import save_images_to_asset_library
                    await save_images_to_asset_library(
                        db, principal.tenant_id,
                        [img.url for img in state.images if img.url],
                    )

            except Exception as img_exc:
                print(f"  ⚠️ 配图处理失败，使用占位图: {img_exc}")
                import re
                def _ph(m):
                    pos = re.search(r'position=(\d+)', m.group(1))
                    idx = int(pos.group(1)) if pos else 1
                    return f'<img src="https://picsum.photos/seed/{task_id[:8]}{idx}/800/400" style="width:100%;border-radius:8px;margin:16px 0;" />'
                content_rich = re.sub(r'\[IMAGE:(.*?)\]', _ph, content)
                footer = article.footer_template or ""
                full_content = f"{content_rich}\n\n---\n\n{footer.strip()}" if footer else content_rich
                if cover_image_url:
                    full_content = f"![封面]({cover_image_url})\n\n{full_content}"
        else:
            import re
            def _ph(m):
                pos = re.search(r'position=(\d+)', m.group(1))
                idx = int(pos.group(1)) if pos else 1
                return f'<img src="https://picsum.photos/seed/{task_id[:8]}{idx}/800/400" style="width:100%;border-radius:8px;margin:16px 0;" />'
            content_rich = re.sub(r'\[IMAGE:(.*?)\]', _ph, content)
            footer = article.footer_template or ""
            full_content = f"{content_rich}\n\n---\n\n{footer.strip()}" if footer else content_rich
            if cover_image_url:
                full_content = f"![封面]({cover_image_url})\n\n{full_content}"

        save_content(db, task_id, content, full_content,
                     cover_image=cover_image_url,
                     footer_template=article.footer_template)
        print(f"  ✅ 正文已保存 (content_len={len(content)})")

    except Exception as exc:
        logger.warning("Content generation failed: %s", exc)
        print(f"  ❌ 正文生成失败: {exc}\n")
        import traceback
        traceback.print_exc()

    return {"message": "Outline confirmed", "task_id": task_id}


@router.post("/articles/{task_id}/publish-draft")
def publish_article_draft(
    task_id: str,
    account_id: int = Query(..., description="WeChat account ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Save a completed article as a WeChat draft (草稿)."""
    article = db.query(Article).filter(Article.task_id == task_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if not (article.full_content or article.content):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Article has no content")

    from app.services.wechat_publisher import save_article_as_draft

    try:
        result = save_article_as_draft(db, article, account_id)
        article.status = "published"
        article.phase = "PUBLISHED"
        db.commit()
        return {"success": True, "media_id": result.get("media_id"), "task_id": task_id}
    except Exception as exc:
        logger.error("Save draft failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/articles/{task_id}/ai-modify-outline")
def ai_modify_outline(
    task_id: str,
    req: AiModifyOutlineRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """AI modify outline (VIP feature)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="AI outline modification not yet implemented")


@router.get("/articles/{task_id}", response_model=ArticleResponse)
def get_article(
    task_id: str,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get article detail by task_id."""
    article = db.query(Article).filter(Article.task_id == task_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return article


@router.get("/articles", response_model=ArticleListResponse)
def list_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """List articles with pagination."""
    query = db.query(Article)
    if status:
        query = query.filter(Article.status == status)
    total = query.count()
    items = query.order_by(Article.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ArticleListResponse(total=total, page=page, page_size=page_size, items=items)


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Delete an article by id."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    db.delete(article)
    db.commit()


@router.get("/articles/{task_id}/progress")
async def article_progress_stream(
    task_id: str,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """SSE progress stream for article generation."""
    article = db.query(Article).filter(Article.task_id == task_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    async def event_stream() -> AsyncGenerator[str, None]:
        phase = article.phase or "pending"
        status_ = article.status or "pending"
        print(f"  [SSE] 连接 task_id={task_id}  phase={phase}  status={status_}")

        if status_ == "completed" or phase in ("ALL_COMPLETE", "COMPLETED", "content_generated"):
            # Only send content events — skip TITLES/OUTLINE to avoid phase flicker
            if article.full_content:
                yield f"event: AGENT3_STREAMING\ndata: {json.dumps({'content': article.full_content})}\n\n"
            elif article.content:
                yield f"event: AGENT3_STREAMING\ndata: {json.dumps({'content': article.content})}\n\n"
            yield "event: ALL_COMPLETE\ndata: {}\n\n"
            await asyncio.sleep(5)
            return

        if status_ == "failed" or phase == "failed":
            yield f"event: ERROR\ndata: {article.error_message or 'Generation failed'}\n\n"
            await asyncio.sleep(5)
            return

        if phase in ("title_generated", "TITLE_SELECTING") and article.title_options:
            yield f"event: TITLES_GENERATED\ndata: {json.dumps({'title_options': article.title_options})}\n\n"

        if phase in ("outline_generated", "OUTLINE_EDITING") and article.outline:
            yield f"event: OUTLINE_GENERATED\ndata: {json.dumps(article.outline)}\n\n"

        if article.content:
            yield f"event: AGENT3_STREAMING\ndata: {json.dumps({'content': article.content})}\n\n"
        if article.full_content:
            yield "event: ALL_COMPLETE\ndata: {}\n\n"
            await asyncio.sleep(5)
            return

        yield f"event: AGENT1_COMPLETE\ndata: {json.dumps({'status': status_, 'phase': phase})}\n\n"

        for _ in range(30):
            yield ": heartbeat\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/articles/{task_id}/logs", response_model=List[AgentLogResponse])
def get_article_logs(
    task_id: str,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get execution logs for an article generation task."""
    logs = db.query(AgentLog).filter(AgentLog.task_id == task_id).order_by(AgentLog.id.asc()).all()
    return logs
