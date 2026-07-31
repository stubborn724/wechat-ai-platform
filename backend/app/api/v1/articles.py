"""Article generation routes (from ai-passage-creator)"""

import asyncio
import json
import logging
import re
from urllib.parse import urlparse
import time
from datetime import datetime
from typing import AsyncGenerator, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.config import settings
from app.models.mysql_models import AgentLog, Article, ContentVersion, WeChatAccount

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---

class CreateArticleRequest(BaseModel):
    topic: str
    content_type: str = "article"  # article / image / pure_image / video
    style: Optional[str] = None
    image_source: str = "DASHSCOPE"
    enabled_image_methods: Optional[List[str]] = None
    user_description: Optional[str] = None
    # 文章生成已收敛为单一全自动链路，拒绝旧客户端的手动协作模式。
    mode: Literal["auto"] = "auto"
    article_count: int = 1
    account_ids: Optional[List[int]] = None  # 要发布到的公众号列表，可多选
    publish_mode: str = "draft"  # 发布模式: "draft" 存草稿箱, "direct" 直接发布
    knowledge_base_ids: Optional[List[int]] = None  # 知识库ID列表，用于注入参考内容
    source_feed_id: Optional[int] = None  # Feed源ID，用于仿写模式
    feed_article_ids: Optional[List[int]] = None  # 具体要仿写的文章ID列表
    selected_image_urls: Optional[List[str]] = None  # 本地素材预选图片URL
    selected_cover_image_url: Optional[str] = None  # 本地素材库或 ERP 导入后选定的封面图
    footer_template: Optional[str] = None  # 文章底部固定内容
    watermark_enabled: Optional[bool] = None  # 是否加水印，None 则使用租户全局配置
    # 视频专用字段
    duration_sec: Optional[int] = None  # 视频时长（秒）
    aspect_ratio: Optional[str] = None  # 画面比例


class ArticleResponse(BaseModel):
    id: int
    task_id: str
    tenant_id: Optional[int] = None
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
    footer_template: Optional[str] = None
    msg_data_id: Optional[str] = None
    publish_id: Optional[str] = None
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

def _strip_photography_text(text: str, pre_extracted_keywords: list | None = None) -> str:
    """Remove lines containing photography/image description language from article body."""
    if not text:
        return text

    # HTML 仿写正文通常只有一行，且包含正文与原位图片节点。按文本行检查摄影词
    # 会把整篇 HTML 当作图片描述删除；HTML 图片信息已在前置 Agent 阶段处理，
    # 此兼容清理只适用于 Markdown/纯文本内容。
    if re.search(r"<[^>]+>", text):
        return text

    # Use pre-extracted keywords, or extract from [IMAGE:] markers + markdown alt text
    image_keywords = pre_extracted_keywords or re.findall(r'keywords=([^,\]]+)', text)
    alt_texts = re.findall(r'!\[([^\]]+)\]\([^)]+\)', text)
    image_keywords.extend(alt_texts)
    text = re.sub(r'\[IMAGE:[^\]]*\]', '', text)
    photo_kw = ['俯拍', '仰拍', '侧拍', '微距', '特写', '近景', '远景', '暖光',
                '逆光', '侧光', '打光', '布光', '景深', '光圈', '快门', '45度']
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            cleaned.append(line)
            continue
        # Always preserve markdown image lines AND HTML img tags
        if re.match(r'^!\[.*\]\(.*\)$', s) or re.match(r'^<img\s+[^>]+/?>$', s, re.IGNORECASE):
            cleaned.append(line)
            continue
        # Remove if line matches any extracted image keyword
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
        if re.search(r'(?:右下角|左下角|右上角|左上角).*(?:水印|logo|标志|文字)', s, re.IGNORECASE):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _render_image_markers(content: str, task_id: str) -> str:
    """清理未生成图片的标记，避免旧入口用随机图库伪装生成结果。

    此函数仅由旧同步流程在没有可用图片 URL 时调用。过去会在这里把每个
    ``[IMAGE:...]`` 标记替换为 Picsum 随机图，造成文章看似生成成功，实际画面
    与主题和参考图完全无关。新的约束是：图片生成失败时正文仍可保存，但图片槽位
    必须为空，并在后端日志中保留任务编号供排障。
    """
    import re as _re
    marker_count = len(_re.findall(r'\[IMAGE:[^\]]*\]', content))
    result = _re.sub(r'\[IMAGE:[^\]]*\]', '', content)
    result = _re.sub(r'\n{3,}', '\n\n', result).strip()
    logger.error(
        "旧文章流程图片生成结果缺失，已移除图片槽位 task=%s image_slots=%s；"
        "随机图库回退已阻止，请检查万相诊断日志",
        task_id,
        marker_count,
    )
    return result


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

async def _run_outline_agent(state):
    """Run agent2 (outline generation). Returns outline dict."""
    from app.services.article_agent_service import AGENT2_OUTLINE_PROMPT, agent2_generate_outline

    _require_api_key()
    t0 = time.perf_counter()
    state = await agent2_generate_outline(state)
    elapsed = int((time.perf_counter() - t0) * 1000)

    section_count = (
        str(len(state.layout_template.sections))
        if getattr(state, 'layout_template', None) and state.layout_template.sections
        else "4-6"
    )
    prompt = AGENT2_OUTLINE_PROMPT.format(
        topic=state.topic, main_title=state.title.main_title,
        sub_title=state.title.sub_title, style=state.style or "default",
        user_description=state.user_description or "无", style_section="",
        section_count=section_count,
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


def _require_generated_content(content: str, generation_error: Optional[str] = None) -> str:
    """校验正文 Agent 的初始输出，保证后续图片流程有可处理的正文。

    HTML 仿写会先生成带 ``__AI_IMAGE_SLOT_*__`` 的 DOM，再由图片 Agent 原位
    回填真实地址。因此本阶段只能校验正文非空，不能将图片槽位视为异常。
    """
    normalized_content = (content or "").strip()
    if not normalized_content:
        detail = generation_error or "模型未返回可用正文"
        raise ValueError(f"正文生成失败，已中止后续发布：{detail}")
    return normalized_content


def _require_publishable_content(content: str, generation_error: Optional[str] = None) -> str:
    """校验图片回填与保存后的最终正文，阻止无效 HTML 发往微信中转站。

    该函数只能在 Agent4/Agent5 完成、图片 URL 已写回 DOM 后调用。此时仍存在
    图片槽位意味着图片回填失败，若继续提交，中转站会把占位符当作图片地址并
    拒绝发布。将它和初始正文校验拆分，可避免误伤 HTML 仿写的正常中间状态。
    """
    normalized_content = _require_generated_content(content, generation_error)
    if "__AI_IMAGE_SLOT_" in normalized_content:
        raise ValueError("正文生成失败，已中止后续发布：图片占位符未替换")
    return normalized_content


def select_delivery_image_url(source_url: str, archived_url: str) -> str:
    """选择可交付给前端和微信中转站的图片地址。

    中转站模式会在真正发送前把本地 MinIO 图片临时转成 COS HTTPS 地址，因此必须
    优先使用已经叠加水印和产品署名的归档地址；不能因本地地址不是 HTTPS 又回退到
    模型原图，否则用户看到的发布图将丢失署名。直连微信模式仍要求公网 HTTPS，
    此时保留源地址以兼容尚未配置公网对象存储的历史环境。
    """
    normalized_source_url = (source_url or "").strip()
    normalized_archived_url = (archived_url or "").strip()
    if str(settings.wechat_api_channel or "").strip().lower() == "relay":
        return normalized_archived_url or normalized_source_url

    parsed_archived_url = urlparse(normalized_archived_url)
    archived_host = (parsed_archived_url.hostname or "").lower()
    is_public_https_archive = (
        parsed_archived_url.scheme == "https"
        and bool(archived_host)
        and archived_host not in {"localhost", "127.0.0.1", "::1"}
    )
    if is_public_https_archive:
        return normalized_archived_url
    return normalized_source_url or normalized_archived_url

@router.post("/articles/create", status_code=status.HTTP_201_CREATED)
async def create_article(
    req: CreateArticleRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Create article / image / video content based on content_type."""
    content_type = req.content_type or "image_text"

    # ========== 纯图片 ==========
    if content_type in ("pure_image", "image"):
        # 有仿写参考文章时，使用 Agent 仿写流程
        if req.feed_article_ids:
            print(f"\n{'='*60}")
            print(f"  [纯图片仿写] 使用 Agent 仿写流程 feed_article_ids={req.feed_article_ids}")
            print(f"{'='*60}")
            try:
                from app.agent.nodes.title_imitation_node import imitate_title
                from app.agent.nodes.image_understanding_node import understand_images
                from app.agent.nodes.prompt_crafting_node import craft_prompt
                from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
                from functools import partial
                from app.services.image_generation_service import image_generation_service
                from app.services.asset_archive_service import save_image_to_asset_library
                from app.services.reference_image_imitation_service import imitate_reference_images
                from app.services.reference_media_analysis_service import extract_markdown_image_urls
                from app.models.mysql_models import FeedSourceArticle as FSA

                ref_articles = db.query(FSA).filter(FSA.id.in_(req.feed_article_ids)).all()
                ref = ref_articles[0] if ref_articles else None
                ref_title = ref.title if ref else ""
                ref_body = ref.body_markdown if ref else ""
                image_urls = extract_markdown_image_urls(ref_body)

                print(f"  参考文章: {ref_title}")
                print(f"  提取图片: {len(image_urls)} 张")

                if not image_urls:
                    return {"type": "content_job", "error": "参考文章中没有图片", "status": "fail"}

                # Agent 1: 标题（用户没输入才仿写）
                new_title = req.topic or ""
                if not new_title:
                    titles = imitate_title(ref_title, topic=req.topic or "图片", count=3)
                    new_title = titles[0] if titles else ref_title
                print(f"  标题: {new_title}")

                # 视觉理解、二维码过滤、提示词构建与图片生成统一由共享服务编排，
                # 保证即时任务和定时任务不会因各自维护循环而出现规则漂移。
                imitation_result = await imitate_reference_images(
                    image_urls,
                    new_title,
                    tenant_id=principal.tenant_id,
                    understand_images_fn=understand_images,
                    craft_prompt_fn=craft_prompt,
                    fallback_prompt_fn=build_wanxiang_prompt,
                    generate_image_fn=partial(
                        image_generation_service.generate_image,
                        tenant_id=principal.tenant_id,
                    ),
                    archive_image_fn=lambda tenant_id, image_url, **kwargs: save_image_to_asset_library(
                        db,
                        tenant_id,
                        image_url,
                        **kwargs,
                    ),
                )
                gen_urls = list(imitation_result.generated_urls)
                if imitation_result.skipped_qrcode_count:
                    print(f"  🚫 已跳过 {imitation_result.skipped_qrcode_count} 张二维码参考图")

                if not gen_urls:
                    error = "参考图片均为二维码，已跳过仿写" if (
                        imitation_result.skipped_qrcode_count == len(image_urls)
                    ) else "所有非二维码图片生成失败"
                    return {"type": "content_job", "error": error, "status": "fail"}

                # 保存到微信草稿箱
                if req.publish_mode in ("draft", "direct") and req.account_ids:
                    print(f"\n  发布到微信草稿箱 accounts={req.account_ids} mode={req.publish_mode}")
                    from app.models.mysql_models import Article as ArtModel
                    from app.services.wechat_publisher import publish_article
                    body_md = "\n\n".join(f"![]({u})" for u in gen_urls)
                    article = ArtModel(
                        task_id=f"img_feed_{ref.id if ref else 0}",
                        tenant_id=principal.tenant_id,
                        main_title=new_title,
                        content=body_md,
                        full_content=body_md,
                        cover_image=gen_urls[0],
                    )
                    for aid in req.account_ids:
                        try:
                            pub = publish_article(db, article, aid, mode=req.publish_mode,
                                                  tenant_id=principal.tenant_id, actor_id=principal.user_id)
                            print(f"  ✅ 微信发布成功 account={aid}: media_id={pub.get('media_id','?')}")
                        except Exception as pbe:
                            print(f"  ⚠️ 微信发布失败 account={aid}: {pbe}")

                return {
                    "type": "content_job",
                    "content_type": "image",
                    "status": "published",
                    "result": {"image_urls": gen_urls, "images": gen_urls, "count": len(gen_urls)},
                    "title": new_title,
                }
            except Exception as e:
                print(f"  ❌ 仿写流程失败: {e}")
                import traceback
                traceback.print_exc()
                return {"type": "content_job", "error": str(e), "status": "fail"}

        # 无仿写参考，走旧流程
        from app.models.mysql_models import ContentJob as CJob
        import uuid as _uuid

        job = CJob(
            tenant_id=principal.tenant_id,
            topic=req.topic or "",
            content_type="image",
            status="queued",
            version=1,
            approval_mode="auto",
            idempotency_key=f"img_{_uuid.uuid4().hex}",
            created_by=principal.user_id,
            generation_config={
                "aspect_ratio": "3:4",
                "brand_style": req.style or "简约现代",
                "target_audience": req.user_description or "",
                "publish_mode": req.publish_mode or "draft",
                "account_ids": req.account_ids or [],
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        print(f"\n{'='*60}")
        print(f"  🖼️ 纯图片任务: job_id={job.id}, 主题={req.topic}")
        print(f"{'='*60}")

        try:
            from app.tasks.content_tasks import _process_image_job_sync
            result = await _process_image_job_sync(db, job, req)
            db.commit()
            return {
                "type": "content_job",
                "job_id": job.id,
                "content_type": "image",
                "status": job.status,
                "result": result,
            }
        except Exception as e:
            print(f"  ❌ 图片处理失败: {e}")
            import traceback
            traceback.print_exc()
            job.status = "fail"
            db.commit()
            return {"type": "content_job", "job_id": job.id, "content_type": "image", "status": "fail", "error": str(e)}

    # ========== 视频 ==========
    if content_type == "video":
        from app.models.mysql_models import ContentJob as CJob
        import uuid as _uuid

        job = CJob(
            tenant_id=principal.tenant_id,
            topic=req.topic or "",
            content_type="video",
            status="queued",
            version=1,
            approval_mode="auto",
            idempotency_key=f"vid_{_uuid.uuid4().hex}",
            created_by=principal.user_id,
            generation_config={
                "duration_sec": req.duration_sec or 30,
                "aspect_ratio": req.aspect_ratio or "9:16",
                "target_audience": req.user_description or "",
                "publish_mode": req.publish_mode or "draft",
                "account_ids": req.account_ids or [],
                "knowledge_base_ids": req.knowledge_base_ids or [],
                "source_feed_id": req.source_feed_id,
                "feed_article_ids": req.feed_article_ids or [],
                "style": req.style,
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        from app.services.storage_service import generate_object_key as _gen_key, storage_service as _ss

        # 同步执行视频处理（通义万相文生视频 API 直接生成）
        try:
            job.status = "generating"
            db.commit()
            print(f"\n{'='*60}")
            print(f"  [视频 {job.id}] 开始处理: {req.topic}")
            print(f"{'='*60}")

            config = job.generation_config or {}
            dur = config.get("duration_sec", 5)
            ar = config.get("aspect_ratio", "9:16")
            size = "720*1280" if ar == "9:16" else "1280*720"

            prompt = req.topic or ""
            if req.user_description:
                prompt = f"{prompt}，{req.user_description}"

            print(f"  >>> 提交文生视频: {prompt[:80]}")
            from app.services.video_gen_service import video_gen_service as _vgen
            video_url = await _vgen.generate_video(prompt=prompt, size=size, duration=dur)
            if not video_url:
                raise RuntimeError("视频生成失败，请检查 API Key 是否有万相视频模型权限")

            print(f"  ✅ 视频生成完毕")

            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=120) as _client:
                resp = await _client.get(video_url)
                resp.raise_for_status()
                video_bytes = resp.content

            vk = _gen_key(principal.tenant_id, f"video_{_uuid.uuid4().hex[:8]}.mp4", prefix="content")
            _ss.upload_bytes(vk, video_bytes, "video/mp4")
            vu = _ss.get_url(vk)
            print(f"  ✅ 视频已保存: {vu[:60]}")

            # 用 AI 生成视频封面
            cover_url = ""
            try:
                from app.services.image_generation_service import image_generation_service
                from app.services.asset_archive_service import save_image_to_asset_library
                cover_prompt = f"{req.topic}，封面图，视觉冲击力，高清，适合做视频封面"
                print(f"  >>> 生成封面: {req.topic}")
                _cover_img_url = await image_generation_service.generate_image(
                    cover_prompt,
                    size="720*1280",
                    tenant_id=principal.tenant_id,
                )
                if _cover_img_url:
                    _asset = await save_image_to_asset_library(db, principal.tenant_id, _cover_img_url, keywords=f"video_cover_{job.id}")
                    if _asset and _asset.storage_key:
                        cover_url = _ss.get_url(_asset.storage_key)
                        print(f"  ✅ 封面已生成")
                else:
                    print(f"  ⚠️ 封面生成失败")
            except Exception as e:
                print(f"  ⚠️ 封面生成异常: {e}")

            pm = config.get("publish_mode", "")
            aids = config.get("account_ids", [])
            if pm in ("draft", "direct") and aids:
                from app.services.wechat_publisher import publish_article
                from app.models.mysql_models import Article as _ArtModel

                art = _ArtModel(
                    task_id=f"vid_{job.id}",
                    tenant_id=principal.tenant_id,
                    main_title=req.topic or "视频",
                    content=f'<p><video src="{vu}" controls style="width:100%" /></p>',
                    full_content=f'<p><video src="{vu}" controls style="width:100%" /></p>',
                    cover_image=cover_url,
                )
                for aid in aids:
                    try:
                        publish_article(db, art, aid, mode=pm,
                                        tenant_id=principal.tenant_id,
                                        actor_id=principal.user_id)
                        print(f"  ✅ 已{'直接发布' if pm == 'direct' else '保存草稿'}到公众号 account={aid}")
                    except Exception as pub_err:
                        print(f"  ⚠️ 微信发布失败 account={aid}: {pub_err}")

            job.status = "published"
            db.commit()
            print(f"  ✅ 视频完成")
            return {"type": "content_job", "job_id": job.id, "content_type": "video",
                    "status": "published", "result": {"video_url": vu}}
        except Exception as e:
            print(f"  ❌ 视频失败: {e}")
            import traceback
            traceback.print_exc()
            job.status = "fail"
            db.commit()
            return {"type": "content_job", "job_id": job.id, "content_type": "video", "status": "fail", "error": str(e)}

    # ========== 图文（原有逻辑） ==========
    from app.services.article_service import create_article as service_create

    article = service_create(
        db=db, user_id=principal.user_id, tenant_id=principal.tenant_id,
        topic=req.topic, style=req.style or "", image_source=req.image_source,
        footer_template=req.footer_template,
    )
    print(f"\n{'='*60}")
    print(f"  📝 创建文章: task_id={article.task_id}")
    print(f"  主题: {req.topic}  风格: {req.style or 'default'}")
    print(f"{'='*60}")

    try:
        from app.schemas.article import ArticleState

        state = ArticleState(
            task_id=article.task_id, user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            topic=req.topic, style=req.style or "default",
            enabled_image_methods=req.enabled_image_methods or ["DASHSCOPE"],
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
        reference_article_title = ""
        if req.source_feed_id and req.feed_article_ids:
            try:
                from app.models.mysql_models import FeedSourceArticle
                articles_to_imitate = (
                    db.query(FeedSourceArticle)
                    .filter(
                        FeedSourceArticle.id.in_(req.feed_article_ids),
                        FeedSourceArticle.feed_source_id == req.source_feed_id,
                        FeedSourceArticle.tenant_id == principal.tenant_id,
                    )
                    .all()
                )
                if articles_to_imitate:
                    reference_article_title = (articles_to_imitate[0].title or "").strip()
                    # 一篇投喂文章决定最终版式。多篇文章仍可作为文字风格参考，但不
                    # 混合 HTML 模板，避免不同页面结构相互覆盖造成图文顺序错乱。
                    state.reference_html = articles_to_imitate[0].body_html or None
                    ref_texts = []
                    for a in articles_to_imitate:
                        title = a.title or ""
                        # 来源账号的购买提示、电话和二维码不能成为语言仿写样本，
                        # 否则即使最终页脚替换成功，正文 Agent 仍可能复写来源联系卡。
                        from app.services.reference_contact_filter_service import (
                            strip_reference_contact_markdown,
                        )

                        body = strip_reference_contact_markdown(a.body_markdown or "")
                        # Clean: strip [IMAGE:], photography lines, then truncate to 300 chars
                        body = re.sub(r'\[IMAGE:[^\]]*\]', '', body)
                        body = re.sub(r'^.*?(?:45度|俯拍|仰拍|微距|特写|暖光|逆光|打光|布光).*?(?:场景|效果|展示|组合|特写).*?\n', '', body, flags=re.MULTILINE)
                        body = body.strip()[:300]
                        if body and len(body) > 50:
                            ref_texts.append(f"## {title}\n\n{body}")
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
                    FeedSource.id == req.source_feed_id,
                    FeedSource.tenant_id == principal.tenant_id,
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

        # ========== AUTO MODE: 自动确定标题，再生成大纲与正文 ==========
        if req.mode == "auto":
            from app.schemas.article import SelectedTitle
            from app.services.article_service import save_outline, save_content

            user_topic = (req.topic or "").strip()
            if user_topic:
                # 用户明确给出主题时，主题就是本次创作方向，保持现有自动生成行为。
                main_title = user_topic
                sub_title = ""
                title_options = [{"main_title": main_title, "sub_title": sub_title}]
                print(f"  ▶ [自动] 使用用户主题作为标题: {main_title}")
            else:
                # 未输入主题时，必须先由标题仿写 Agent 产出原创标题。禁止将投喂
                # 文章原题作为回退值，否则后续大纲和正文会变成围绕原题的复述。
                if not reference_article_title:
                    raise ValueError("未填写主题且未选择包含标题的投喂文章，无法生成文章")
                if not settings.dashscope_api_key:
                    raise RuntimeError("未配置模型密钥，无法为投喂文章生成仿写标题")

                from app.services.article_agent_service import agent1_generate_imitation_title

                print("  ▶ [自动] 调用标题仿写 Agent...")
                state = await agent1_generate_imitation_title(state, reference_article_title)
                if state.error or not state.title_options:
                    raise ValueError(state.error or "标题仿写 Agent 未返回可用标题")

                selected_option = state.title_options[0]
                main_title = selected_option.main_title.strip()
                sub_title = selected_option.sub_title.strip()
                title_options = [option.model_dump() for option in state.title_options]
                print(f"  ▶ [自动] 标题仿写完成: {main_title}")

            state.topic = main_title
            state.title = SelectedTitle(main_title=main_title, sub_title=sub_title)
            article.topic = main_title
            article.main_title = main_title
            article.sub_title = sub_title
            article.title_options = title_options
            db.commit()

            # Generate outline
            if settings.dashscope_api_key:
                outline_data = await _run_outline_agent(state)
            else:
                outline_data = _sample_outline(state.topic, main_title)
            save_outline(db, article.task_id, outline_data)
            print(f"  ▶ [自动] 大纲已生成")

            # Generate content
            if settings.dashscope_api_key:
                content = await _run_content_agent(state)
                state.content = content
            else:
                content = _sample_content(main_title, sub_title, outline_data)
            content = _require_generated_content(content, state.error)
            state.content = content
            print(f"  ▶ [自动] 正文已生成 ({len(content)} chars)")

            # ===== Generate images (Agent4+Agent5) & auto-archive =====
            # 封面与正文配图是两条独立数据流。手动选择的本地/ERP 图片只作为封面，
            # 正文配图仍由 enabled_image_methods 和 Agent4/Agent5 决定。
            cover_image_url = req.selected_cover_image_url or None
            from app.services.image_generation_service import is_image_generation_configured

            if is_image_generation_configured():
                try:
                    from app.services.article_agent_service import (
                        agent4_analyze_image_requirements,
                        agent5_generate_images,
                        merge_images_into_content,
                    )
                    # 仅在用户未指定本地/ERP 封面时调用万相生成，避免覆盖明确选择。
                    if cover_image_url:
                        print(f"  ▶ [自动] 使用已选择的封面图: {cover_image_url[:80]}")
                    else:
                        print("  ▶ [自动] 生成AI封面图...")
                        try:
                            from app.services.image_generation_service import image_generation_service
                            cover_prompt = f"公众号文章封面图：{main_title}。扁平化设计，简洁大气，适合社交媒体传播。不要包含任何文字或标题。"
                            cover_url = await image_generation_service.generate_image(
                                cover_prompt,
                                size="1024*1024",
                                tenant_id=principal.tenant_id,
                            )
                            if cover_url:
                                cover_image_url = cover_url
                                print(f"  ✅ AI封面图生成成功: {cover_url[:60]}")
                            else:
                                print(f"  ⚠️ AI封面生成失败，将使用正文配图")
                        except Exception as cover_err:
                            print(f"  ⚠️ AI封面图生成异常: {cover_err}")

                    # Detect pure-image gallery BEFORE agent4 (which may hang)
                    is_gallery = state.content and all(
                        l.strip().startswith('[IMAGE:') and 'type=gallery' in l
                        for l in state.content.split('\n') if l.strip()
                    )
                    if is_gallery:
                        print("  ▶ [自动] 纯图画廊模式，跳过配图获取，使用占位图...")
                        image_keywords_auto = re.findall(r'keywords=([^,\]]+)', content)
                        content_rich = _render_image_markers(state.content, state.task_id)
                    else:
                        print("  ▶ [自动] 分析配图需求...")
                        state = await agent4_analyze_image_requirements(state)
                        print(f"     需要 {len(state.image_requirements)} 张配图")

                        print("  ▶ [自动] 获取配图...")
                        state = await agent5_generate_images(state)
                        print(f"     已获取 {len(state.images)} 张配图")

                    # Extract image keywords BEFORE merge (post-processing needs them)
                    image_keywords_auto = re.findall(r'keywords=([^,\]]+)', content)

                    # Save images to asset library (include cover image)
                    if state.images:
                        from app.services.asset_archive_service import save_images_to_asset_library
                        from app.services.article_publication_polish_service import (
                            build_article_image_attribution,
                        )
                        image_urls = [img.url for img in state.images if img.url]
                        # 手动封面已经存在本地素材库，无需再次下载归档形成重复素材。
                        if (
                            cover_image_url
                            and not req.selected_cover_image_url
                            and cover_image_url not in image_urls
                        ):
                            image_urls.append(cover_image_url)
                        print(f"  ▶ [自动] 归档 {len(image_urls)} 张素材到素材库...")
                        archived = await save_images_to_asset_library(
                            db, principal.tenant_id, image_urls,
                            watermark_enabled=req.watermark_enabled,
                            article_image_attribution=build_article_image_attribution(
                                state.product_name or main_title,
                            ),
                        )
                        # 归档始终执行；文章交付地址则必须保证微信中转站能够访问。
                        from app.services.storage_service import storage_service as _ss
                        url_map: dict[str, str] = {}
                        for orig_url, asset_obj in zip(image_urls, archived):
                            if asset_obj:
                                url_map[orig_url] = select_delivery_image_url(
                                    orig_url,
                                    _ss.get_url(asset_obj.storage_key),
                                )
                        # 公网 HTTPS MinIO 使用水印归档图；本机 HTTP MinIO 保留万相原图。
                        if url_map:
                            for img in state.images:
                                if img.url and img.url in url_map:
                                    img.url = url_map[img.url]
                            if cover_image_url and cover_image_url in url_map:
                                cover_image_url = url_map[cover_image_url]
                        print(f"  ✅ 素材归档完成，已水印 {len(url_map)} 张")

                    # Merge images into content AFTER URLs are finalized
                    if state.images:
                        state = merge_images_into_content(state)
                        content_rich = state.full_content or content

                    # 封面图不嵌入正文（已存为 article.cover_image，前端自行展示）
                    # 不要在这里往 content_rich 前面加 <img>，避免重复

                except Exception as img_exc:
                    print(f"  ⚠️ 配图处理失败，使用占位图: {img_exc}")
                    content_rich = _render_image_markers(content, article.task_id)
            else:
                content_rich = _render_image_markers(content, article.task_id)

            # Footer is already appended by merge_images_into_content() via state.footer_template

            # Post-processing: strip image descriptions using pre-extracted keywords
            content_rich = _strip_photography_text(content_rich, image_keywords_auto)
            # 不让模型或投喂源自行决定 AI 图片说明。统一在最终正文落库前追加，
            # 并且内部已实现幂等，自动保存与重试发布都不会产生多份说明。
            from app.services.article_publication_polish_service import append_ai_image_disclaimer

            content_rich = append_ai_image_disclaimer(content_rich)

            saved_article = save_content(
                db,
                article.task_id,
                content,
                content_rich,
                cover_image=cover_image_url,
                footer_template=req.footer_template,
            )
            # ``save_content`` 仍会兼容处理旧 Markdown 内容。读取持久化后的结果
            # 再次校验，确保任一后处理步骤都不能把空正文继续交给微信中转站。
            _require_publishable_content(
                (saved_article.full_content or saved_article.content) if saved_article else "",
                "保存正文后内容为空",
            )
            if cover_image_url:
                article.cover_image = cover_image_url
            article.status = "completed"
            article.phase = "ALL_COMPLETE"
            db.commit()
            print(f"  ✅ [自动] 全流程完成！")

            # 自动保存到微信公众号（草稿箱或直接发布）
            if req.account_ids:
                from app.services.wechat_publisher import publish_article
                success_count = 0
                for aid in req.account_ids:
                    try:
                        result = publish_article(db, article, aid, mode=req.publish_mode, tenant_id=principal.tenant_id, actor_id=principal.user_id)
                        media_id = result.get("media_id")
                        publish_id = result.get("publish_id")
                        mode_label = "直接发布" if req.publish_mode == "direct" else "存草稿"
                        detail = f"publish_id={publish_id}" if publish_id else f"media_id={media_id}"
                        print(f"  ✅ [自动] {mode_label}到公众号 #{aid} 成功! {detail}")
                        success_count += 1
                    except Exception as draft_err:
                        print(f"  ⚠️ [自动] 发布到公众号 #{aid} 失败: {draft_err}")
                if success_count > 0:
                    article.status = "published" if req.publish_mode == "direct" else "draft_saved"
                    article.phase = "PUBLISHED" if req.publish_mode == "direct" else "DRAFT_SAVED"
                    db.commit()

    except Exception as exc:
        logger.warning("Article generation pipeline failed: %s", exc)
        print(f"  ❌ 生成失败: {exc}\n")
        import traceback
        traceback.print_exc()
        article.status = "failed"
        article.phase = "FAILED"
        article.error_message = str(exc)[:2000]
        db.commit()

    return article


@router.post("/articles/{task_id}/publish-draft")
def publish_article_draft(
    task_id: str,
    account_id: int = Query(..., description="WeChat account ID"),
    mode: str = Query("draft", description='发布模式: "draft" 存草稿箱, "direct" 直接发布'),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """发布文章到微信公众号（存草稿箱或直接发布）"""
    article = db.query(Article).filter(Article.task_id == task_id, Article.tenant_id == principal.tenant_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if not (article.full_content or article.content):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Article has no content")

    # 验证公众号属于当前租户
    account = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.tenant_id == principal.tenant_id,
    ).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    from app.services.wechat_publisher import publish_article

    try:
        result = publish_article(db, article, account_id, mode=mode, tenant_id=principal.tenant_id, actor_id=principal.user_id)
        if mode == "direct":
            article.status = "publishing"
            article.phase = "PUBLISHING"
            article.publish_id = result.get("publish_id") or result.get("media_id", "")
        else:
            article.status = "draft_saved"
            article.phase = "DRAFT_SAVED"
        db.commit()
        return {
            "success": True,
            "media_id": result.get("media_id"),
            "publish_id": result.get("publish_id"),
            "task_id": task_id,
            "mode": mode,
        }
    except Exception as exc:
        logger.error("Publish article failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


# ===========================================================
# 文章定时发布 — 已迁移到 scheduled_tasks 流程
# ===========================================================


@router.get("/articles/{task_id}/publish-status")
async def query_publish_status(
    task_id: str,
    account_id: int = Query(..., description="OAuth account ID"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """查询公众号发布任务状态（当前版本的发布改用 publish-draft?mode=direct）

    保留此端点仅用于兼容旧数据，新数据直接通过 publish-draft 完成。
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="此接口已废弃，请使用 POST /articles/{task_id}/publish-draft?mode=direct",
    )


@router.post("/articles/{task_id}/set-msg-data-id")
def set_article_msg_data_id(
    task_id: str,
    msg_data_id: str = Query(..., description="微信文章 msg_data_id"),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """手动设置文章的 msg_data_id（用于在微信后台发布后，手动绑定评论）"""
    article = db.query(Article).filter(
        Article.task_id == task_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    article.msg_data_id = msg_data_id
    db.commit()
    return {"success": True, "task_id": task_id, "msg_data_id": msg_data_id}


@router.get("/articles/{task_id}", response_model=ArticleResponse)
def get_article(
    task_id: str,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """Get article detail by task_id."""
    article = db.query(Article).filter(Article.task_id == task_id, Article.tenant_id == principal.tenant_id).first()
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
    """List articles with pagination, scoped to current tenant."""
    query = db.query(Article).filter(Article.tenant_id == principal.tenant_id)
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
    """Delete an article by id, scoped to current tenant."""
    article = db.query(Article).filter(Article.id == article_id, Article.tenant_id == principal.tenant_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    # Nullify foreign keys before deleting
    db.query(ContentVersion).filter(ContentVersion.article_id == article_id).update(
        {ContentVersion.article_id: None}
    )
    db.delete(article)
    db.commit()


@router.get("/articles/{task_id}/progress")
async def article_progress_stream(
    task_id: str,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """SSE progress stream for article generation."""
    article = db.query(Article).filter(
        Article.task_id == task_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
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
    # 验证文章属于当前租户
    article = db.query(Article).filter(
        Article.task_id == task_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    logs = db.query(AgentLog).filter(AgentLog.task_id == task_id).order_by(AgentLog.id.asc()).all()
    return logs


# =====================================================================
# 阅读指标与质量评分
# =====================================================================


@router.get("/articles/{article_id}/metrics/latest")
def get_article_metrics_latest(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取文章最新阅读指标"""
    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return {
        "article_id": article.id,
        "task_id": article.task_id,
        "read_count": article.latest_read_count or 0,
        "like_count": article.latest_like_count or 0,
        "share_count": article.latest_share_count or 0,
        "comment_count": article.latest_comment_count or 0,
        "fav_count": article.latest_fav_count or 0,
        "updated_at": article.metrics_updated_at,
    }


@router.get("/articles/{article_id}/metrics")
def get_article_metrics_history(
    article_id: int,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取文章历史阅读指标趋势"""
    from app.models.mysql_models import ArticleMetrics

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    query = db.query(ArticleMetrics).filter(
        ArticleMetrics.article_id == article_id,
    )
    if start_date:
        query = query.filter(ArticleMetrics.metric_date >= start_date)
    if end_date:
        query = query.filter(ArticleMetrics.metric_date <= end_date)

    items = query.order_by(ArticleMetrics.metric_date.asc()).all()
    return [
        {
            "date": m.metric_date.isoformat() if hasattr(m.metric_date, 'isoformat') else str(m.metric_date),
            "read_count": m.read_count,
            "like_count": m.like_count,
            "share_count": m.share_count,
            "comment_count": m.comment_count,
        }
        for m in items
    ]


@router.post("/articles/{article_id}/metrics/sync")
def sync_article_metrics(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """手动触发文章指标同步"""
    from app.tasks.metrics_tasks import sync_single_article_metrics

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    sync_single_article_metrics.delay(article_id)
    return {"message": "Metrics sync triggered", "article_id": article_id}


@router.get("/articles/{article_id}/quality/latest")
def get_article_quality_latest(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取文章最新 AI 质量评分"""
    from app.models.mysql_models import ArticleQualityEvaluation

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    evaluation = (
        db.query(ArticleQualityEvaluation)
        .filter(
            ArticleQualityEvaluation.article_id == article_id,
            ArticleQualityEvaluation.status == "success",
        )
        .order_by(ArticleQualityEvaluation.id.desc())
        .first()
    )
    if not evaluation:
        return {"article_id": article_id, "status": "not_evaluated"}

    return {
        "article_id": article_id,
        "overall_score": evaluation.overall_score,
        "dimensions": {
            "content_score": evaluation.content_score,
            "readability_score": evaluation.readability_score,
            "structure_score": evaluation.structure_score,
            "value_score": evaluation.value_score,
            "title_score": evaluation.title_score,
            "title_consistency_score": evaluation.title_consistency_score,
            "credibility_score": evaluation.credibility_score,
        },
        "issues": evaluation.issues,
        "suggestions": evaluation.suggestions,
        "confidence": evaluation.confidence,
        "evaluated_at": evaluation.evaluated_at,
    }


@router.get("/articles/{article_id}/quality-evaluations")
def get_article_quality_history(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """获取文章历史评分记录"""
    from app.models.mysql_models import ArticleQualityEvaluation

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    evals = (
        db.query(ArticleQualityEvaluation)
        .filter(
            ArticleQualityEvaluation.article_id == article_id,
        )
        .order_by(ArticleQualityEvaluation.id.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": e.id,
            "overall_score": e.overall_score,
            "model_name": e.model_name,
            "prompt_version": e.prompt_version,
            "status": e.status,
            "evaluated_at": e.evaluated_at,
        }
        for e in evals
    ]


@router.post("/articles/{article_id}/quality-evaluations")
def trigger_quality_evaluation(
    article_id: int,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """手动触发文章质量评分"""
    from app.tasks.quality_tasks import evaluate_article_quality

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    evaluate_article_quality.delay(article_id)
    return {"message": "Quality evaluation triggered", "article_id": article_id}


# =====================================================================
# 优化稿
# =====================================================================


@router.post("/articles/{article_id}/optimization-drafts")
async def create_optimization_draft(
    article_id: int,
    req: dict,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """为文章创建优化草稿"""
    from app.services.article_optimization_service import optimization_service

    article = db.query(Article).filter(
        Article.id == article_id,
        Article.tenant_id == principal.tenant_id,
    ).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    opt_type = req.get("optimization_type", "structure_optimize")
    instruction = req.get("instruction", "")
    evaluation_id = req.get("evaluation_id", 0)

    try:
        result = await optimization_service.generate(
            db, article, opt_type,
            instruction=instruction,
            evaluation_id=evaluation_id or None,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
