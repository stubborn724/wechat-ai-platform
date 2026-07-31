"""内容类型适配器 — 为图文/纯图片/视频提供统一的生成和发布接口"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import ContentJob, ContentJobArticle, ContentVersion

logger = logging.getLogger(__name__)


# ============================================================================
# Abstract interfaces
# ============================================================================


class ContentGenerator(ABC):
    """内容生成器：根据内容类型生成 AI 内容，返回 ContentVersion 数据"""

    @abstractmethod
    def generate(self, db: Session, job: ContentJob, slot: ContentJobArticle) -> Dict[str, Any]:
        """生成内容，返回 dict 包含 title, body_markdown, summary, images, cover_url 等"""
        ...


class WeChatPublisherAdapter(ABC):
    """微信发布适配器：根据内容类型调用不同微信 API"""

    @abstractmethod
    def publish(self, db: Session, article_data: Dict[str, Any],
                account_id: int, tenant_id: int) -> Dict[str, Any]:
        """发布到微信，返回 media_id / publish_id 等"""
        ...


# ============================================================================
# 图文适配器 — 复用现有 5-agent 流水线
# ============================================================================


class ImageTextGenerator(ContentGenerator):
    """图文生成器：使用 5-agent 流水线（标题→大纲→正文→配图）"""

    def generate(self, db: Session, job: ContentJob, slot: ContentJobArticle) -> Dict[str, Any]:
        """Run the full agent pipeline for one article slot."""
        import asyncio
        from app.schemas.article import ArticleState
        from app.services.article_agent_service import (
            agent1_generate_title_options,
            agent2_generate_outline,
            agent3_generate_content,
            agent4_analyze_image_requirements,
            agent5_generate_images,
            merge_images_into_content,
        )
        from app.config import settings

        config = job.generation_config or {}

        state = ArticleState(
            task_id=f"job_{job.id}_{slot.sort_order}",
            user_id=job.created_by or 0,
            tenant_id=job.tenant_id,
            topic=job.topic or "",
            style=config.get("style", "default"),
            footer_template=job.footer_template,
        )

        async def _run():
            # Writing mode setup
            writing_mode = config.get("writing_mode", "free")
            if writing_mode == "feed":
                feed_source_ids = config.get("feed_source_ids", [])
                if feed_source_ids:
                    from app.models.mysql_models import FeedSource, FeedSourceArticle
                    sources = (
                        db.query(FeedSource)
                        .filter(FeedSource.id.in_(feed_source_ids))
                        .all()
                    )
                    style_profiles = [s.style_profile for s in sources if s and s.style_profile]
                    if style_profiles:
                        state.style_profile = style_profiles[0]

                    articles = (
                        db.query(FeedSourceArticle)
                        .filter(FeedSourceArticle.feed_source_id.in_(feed_source_ids))
                        .order_by(FeedSourceArticle.id.desc())
                        .limit(3)
                        .all()
                    )
                    if articles:
                        state.reference_articles = [a.body_markdown or "" for a in articles if a.body_markdown]
                        # 首篇文章提供唯一 HTML 版式模板，其余文章只作为文字风格参考。
                        state.reference_html = articles[0].body_html or None

            # Knowledge base context
            kb_ids = config.get("knowledge_base_ids", [])
            if kb_ids:
                try:
                    from app.services.knowledge_base_service import search_knowledge_base
                    from app.database import get_pg_db

                    pg_db = next(get_pg_db())
                    try:
                        all_chunks = []
                        for kb_id in kb_ids:
                            results = search_knowledge_base(pg_db, kb_id, job.topic or "", top_k=3)
                            all_chunks.extend(results)
                        if all_chunks:
                            parts = [f"[来源: 知识库 chunk_id={r['id']}]\n{r['content']}" for r in all_chunks]
                            state.kb_context = "\n\n---\n\n".join(parts)
                    finally:
                        pg_db.close()
                except Exception as exc:
                    logger.warning("KB context load failed for job %d: %s", job.id, exc)

            # Agent pipeline
            title_options = await agent1_generate_title_options(state)
            state.title_options = title_options
            state.title = title_options[0] if title_options else None
            state.user_description = job.topic

            outline = await agent2_generate_outline(state)
            state.outline = outline

            print("  ▶ [Feed] agent3: 生成正文...")
            content = await agent3_generate_content(state)
            state.content = content
            print(f"  ▶ [Feed] agent3: 正文已生成 ({len(content)} chars)")

            # Detect pure-image gallery BEFORE agent4 (which may hang)
            is_gallery = state.content and all(
                line.strip().startswith('[IMAGE:') and 'type=gallery' in line
                for line in state.content.split('\n') if line.strip()
            )
            if is_gallery:
                print("  ▶ [Feed] 纯图画廊模式，跳过 agent4+agent5，使用占位图...")
                from app.api.v1.articles import _render_image_markers
                state.images = []
                state.full_content = _render_image_markers(state.content, state.task_id)
                print(f"  ▶ [Feed] 画廊 HTML 已生成 ({len(state.full_content)} chars)")
            else:
                print("  ▶ [Feed] agent4: 分析配图需求...")
                image_reqs = await agent4_analyze_image_requirements(state)
                state.image_requirements = image_reqs
                print(f"  ▶ [Feed] agent4: 完成，需要 {len(image_reqs)} 张配图")

                print("  ▶ [Feed] agent5: 获取配图...")
                images = await agent5_generate_images(state)
                state.images = images
                print(f"  ▶ [Feed] agent5: 完成，已获取 {len(images)} 张配图")

                full_content = merge_images_into_content(state)
                state.full_content = full_content

            return state

        state = asyncio.run(_run())

        return {
            "title": state.title.main_title if state.title else job.topic,
            "sub_title": state.title.sub_title if state.title else "",
            "body_markdown": state.full_content or state.content or "",
            "summary": job.topic,
            "images": [{"url": img.url, "position": img.position} for img in state.images] if state.images else [],
            "cover_url": next((img.url for img in state.images if img.position == 0), None) if state.images else None,
        }


class ImageTextPublisher(WeChatPublisherAdapter):
    """图文发布器：使用现有 WechatPublisher 的 draft/direct 流程"""

    def publish(self, db: Session, article_data: Dict[str, Any],
                account_id: int, tenant_id: int) -> Dict[str, Any]:
        from app.services.wechat_publisher import publish_article
        from app.models.mysql_models import Article

        article = db.query(Article).filter(Article.id == article_data.get("id")).first()
        if not article:
            raise ValueError(f"Article {article_data.get('id')} not found")

        mode = article_data.get("publish_mode", "draft")
        result = publish_article(db, article, account_id, mode=mode, tenant_id=tenant_id, actor_id=0)


# ============================================================================
# 纯图片适配器
# ============================================================================


class PureImageGenerator(ContentGenerator):
    """纯图片生成器：从素材库/外部 API 选取图片，AI 生成描述"""

    def generate(self, db: Session, job: ContentJob, slot: ContentJobArticle) -> Dict[str, Any]:
        config = job.generation_config or {}

        images = []
        cover_url = None

        # Use images from the job config
        if not images:
            existing_images = config.get("selected_image_urls", [])
            for i, url in enumerate(existing_images):
                if i == 0:
                    cover_url = url
                images.append({"url": url, "position": i + 1})

        return {
            "title": job.topic or "纯图片",
            "body_markdown": "",
            "summary": f"纯图片内容 — {job.topic}" if job.topic else "纯图片内容",
            "images": images,
            "cover_url": cover_url,
            "image_urls": [img["url"] for img in images],
        }


class PureImagePublisher(WeChatPublisherAdapter):
    """纯图片发布器：上传图片到微信永久素材，创建图片草稿"""

    def publish(self, db: Session, article_data: Dict[str, Any],
                account_id: int, tenant_id: int) -> Dict[str, Any]:
        from app.services.wechat_gateway_policy import ensure_direct_wechat_api_allowed
        ensure_direct_wechat_api_allowed("纯图片发布")
        from app.services.wechat_publisher import _get_publisher_for_account

        publisher = _get_publisher_for_account(db, account_id, tenant_id, actor_id=0)
        image_urls = article_data.get("image_urls", [])

        if not image_urls:
            return {"error": "No images to publish", "media_id": ""}

        # Upload first image as cover via WeChat material API
        try:
            cover_media_id = publisher._resolve_cover(image_urls[0], need_upload=False)
        except Exception:
            cover_media_id = ""

        # Format as a pure image draft (minimal HTML with just images)
        body_parts = []
        for url in image_urls:
            body_parts.append(f'<img src="{url}" data-wx-src="{url}" />')
        body_html = "<p>" + "</p><p>".join(body_parts) + "</p>"

        title = article_data.get("title", "图片分享")[:64]

        # Save as draft using internal method
        token = publisher.get_access_token()
        import requests as _req

        draft_body = {
            "title": title,
            "author": "",
            "digest": article_data.get("summary", "")[:120],
            "content": body_html,
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }
        if cover_media_id:
            draft_body["thumb_media_id"] = cover_media_id

        resp = _req.post(
            "https://api.weixin.qq.com/cgi-bin/draft/add",
            params={"access_token": token},
            json=draft_body,
            timeout=15,
        )
        data = resp.json()
        media_id = data.get("media_id", "")
        return {"media_id": media_id, "draft_data": data}


# ============================================================================
# 视频适配器（基础实现，后续可扩展）
# ============================================================================


class VideoGenerator(ContentGenerator):
    """视频生成器：搜索素材视频，生成描述文案"""

    def generate(self, db: Session, job: ContentJob, slot: ContentJobArticle) -> Dict[str, Any]:
        config = job.generation_config or {}
        images = config.get("selected_image_urls", [])

        return {
            "title": job.topic or "视频",
            "body_markdown": "",
            "summary": f"视频内容 — {job.topic}" if job.topic else "视频内容",
            "images": [{"url": url, "position": i} for i, url in enumerate(images)],
            "video_url": config.get("video_url", ""),
            "video_description": job.topic or "",
        }


class VideoPublisher(WeChatPublisherAdapter):
    """视频发布器：上传视频到微信永久素材，创建视频草稿"""

    def publish(self, db: Session, article_data: Dict[str, Any],
                account_id: int, tenant_id: int) -> Dict[str, Any]:
        from app.services.wechat_gateway_policy import ensure_direct_wechat_api_allowed
        ensure_direct_wechat_api_allowed("视频发布")
        from app.services.wechat_publisher import _get_publisher_for_account

        publisher = _get_publisher_for_account(db, account_id, tenant_id, actor_id=0)
        video_url = article_data.get("video_url", "")

        if not video_url:
            return {"error": "No video URL provided", "media_id": ""}

        token = publisher.get_access_token()
        import requests as _req

        # Download video
        video_resp = _req.get(video_url, timeout=60)
        video_resp.raise_for_status()
        video_bytes = video_resp.content

        # Upload as permanent video material
        files = {
            "media": (f"video_{account_id}.mp4", video_bytes, "video/mp4"),
        }
        params = {"access_token": token, "type": "video"}
        upload_resp = _req.post(
            "https://api.weixin.qq.com/cgi-bin/material/add_material",
            params=params,
            files=files,
            timeout=120,
        )
        upload_data = upload_resp.json()
        media_id = upload_data.get("media_id", "")

        if not media_id:
            return {"error": f"Video upload failed: {upload_data}", "media_id": ""}

        # Save as draft with video
        title = article_data.get("title", "视频分享")[:64]
        description = article_data.get("video_description", "")[:120]

        draft_body = {
            "title": title,
            "author": "",
            "digest": description,
            "content": f'<p><img src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/dummy" data-wx-src="" /></p>',
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }

        resp = _req.post(
            "https://api.weixin.qq.com/cgi-bin/draft/add",
            params={"access_token": token},
            json=draft_body,
            timeout=15,
        )
        data = resp.json()
        return {"media_id": data.get("media_id", ""), "video_media_id": media_id, "upload_data": upload_data, "draft_data": data}


# ============================================================================
# 路由表
# ============================================================================

GENERATORS = {
    "image_text": ImageTextGenerator(),
    "pure_image": PureImageGenerator(),
    "video": VideoGenerator(),
}

PUBLISHERS = {
    "image_text": ImageTextPublisher(),
    "pure_image": PureImagePublisher(),
    "video": VideoPublisher(),
}


def get_generator(content_type: str) -> ContentGenerator:
    """按内容类型获取对应生成器，默认回退到图文"""
    return GENERATORS.get(content_type, GENERATORS["image_text"])


def get_publisher(content_type: str) -> WeChatPublisherAdapter:
    """按内容类型获取对应发布器，默认回退到图文"""
    return PUBLISHERS.get(content_type, PUBLISHERS["image_text"])
