"""Feed source service — RSS parsing, article scraping, style analysis, article management."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import FeedSource, FeedSourceArticle
from app.services.url_safety import validate_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoFormatProfileResult:
    """单篇投喂文章的自动格式分析结果。

    抓取与格式分析的容错边界在这里明确：抓取成功是主流程，格式模板只是后续仿写的
    增强能力。因此某篇 HTML 不完整时返回错误信息，由调用方汇总告警，而不是让整个
    投喂源导入回滚。
    """

    created: bool
    profile_id: int | None
    error: str | None = None


def auto_create_format_profile_for_article(
    *,
    db: Session,
    article: FeedSourceArticle,
) -> AutoFormatProfileResult:
    """为已保存的文章自动创建或复用格式模板，并隔离单篇解析错误。"""

    from app.services.format_profile_persistence_service import (
        create_or_reuse_format_profile,
    )

    try:
        persisted = create_or_reuse_format_profile(db, article=article)
    except ValueError as exc:
        logger.warning(
            "跳过投喂文章格式分析: article_id=%s, reason=%s",
            getattr(article, "id", None),
            exc,
        )
        return AutoFormatProfileResult(created=False, profile_id=None, error=str(exc))

    return AutoFormatProfileResult(
        created=persisted.created,
        profile_id=persisted.profile.id,
    )


# ---------------------------------------------------------------------------
# RSS / URL fetching
# ---------------------------------------------------------------------------


async def fetch_source(db: Session, source_id: int, tenant_id: int = 0) -> dict:
    """Fetch articles from a feed source and store them.

    Supports RSS/Atom feeds (via feedparser) and single URLs (via readability).
    """
    query = db.query(FeedSource).filter(FeedSource.id == source_id)
    if tenant_id:
        query = query.filter(FeedSource.tenant_id == tenant_id)
    source = query.first()
    if not source:
        raise ValueError(f"Feed source {source_id} not found (tenant={tenant_id})")

    articles = []
    errors = []

    if source.source_type == "rss" and source.feed_url:
        try:
            articles = await _fetch_rss(source.feed_url)
        except Exception as exc:
            errors.append(f"RSS fetch failed: {exc}")

    elif source.source_type == "url" and source.source_identifier:
        try:
            article = await _fetch_single_url(source.source_identifier)
            if article:
                articles = [article]
        except Exception as exc:
            errors.append(f"URL fetch failed: {exc}")

    elif source.source_type == "official_account":
        # WeChat official account — try source_identifier as the account URL or ID
        if source.source_identifier:
            try:
                article = await _fetch_single_url(source.source_identifier)
                if article:
                    articles = [article]
            except Exception as exc:
                errors.append(f"Official account fetch failed: {exc}")

    # Save articles — update existing or insert new
    saved_count = 0
    format_profiles_created = 0
    format_profile_errors: list[str] = []
    for article_data in articles:
        title = article_data.get("title", "")[:255]
        article_url = article_data.get("link", "")[:512]
        body_md = article_data.get("body_markdown", "")
        body_html = article_data.get("body_html", "")

        existing = db.query(FeedSourceArticle).filter(
            FeedSourceArticle.feed_source_id == source_id,
            FeedSourceArticle.title == title,
        ).first()

        if existing:
            # Update existing so re-fetch replaces stale content
            existing.body_markdown = body_md
            existing.body_html = body_html
            existing.summary = article_data.get("summary", "")[:500]
            existing.article_url = article_url or existing.article_url
            existing.cover_image_url = article_data.get("cover_image_url", "")[:512] or existing.cover_image_url
            existing.published_at = article_data.get("published_at") or existing.published_at
            existing.word_count = len(body_md)
            existing.is_analyzed = False
            persisted_article = existing
        else:
            fa = FeedSourceArticle(
                tenant_id=source.tenant_id,
                feed_source_id=source_id,
                title=title,
                article_url=article_url,
                body_markdown=body_md,
                body_html=body_html,
                summary=article_data.get("summary", "")[:500],
                cover_image_url=article_data.get("cover_image_url", "")[:512],
                published_at=article_data.get("published_at"),
                word_count=len(body_md),
                is_analyzed=False,
            )
            db.add(fa)
            # 新建文章需要先得到主键，格式模板外键才能在同一个事务中安全引用它。
            db.flush()
            persisted_article = fa

        format_result = auto_create_format_profile_for_article(
            db=db,
            article=persisted_article,
        )
        if format_result.created:
            format_profiles_created += 1
        if format_result.error:
            format_profile_errors.append(
                f"《{title or '未命名文章'}》格式分析失败: {format_result.error}"
            )
        saved_count += 1

    # Update last_fetched_at
    source.last_fetched_at = datetime.utcnow()
    db.commit()

    return {
        "source_id": source_id,
        "source_name": source.name,
        "articles_fetched": len(articles),
        "articles_saved": saved_count,
        "format_profiles_created": format_profiles_created,
        "format_profile_errors": format_profile_errors,
        "errors": errors,
    }


async def _fetch_rss(feed_url: str) -> List[dict]:
    """Parse an RSS/Atom feed and return article entries."""
    import feedparser

    logger.info("Fetching RSS feed: %s", feed_url)
    validate_url(feed_url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(feed_url, headers=_headers())
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)

    entries = []
    for entry in feed.entries:
        link = entry.get("link", "")
        published = entry.get("published_parsed")
        pub_dt = None
        if published:
            try:
                import time as _time
                pub_dt = datetime.fromtimestamp(_time.mktime(published))
            except Exception:
                pass

        body_md = ""
        body_html = ""

        # RSS entry may already have full content in content, summary or description
        rss_content = ""
        if hasattr(entry, "content") and entry.content:
            rss_content = entry.content[0].get("value", "")
        elif entry.get("summary"):
            rss_content = entry.get("summary", "")
        elif entry.get("description"):
            rss_content = entry.get("description", "")

        if rss_content and len(rss_content) > 200:
            # Entry has substantial content — convert directly, skip scraping
            body_html = rss_content
            body_md = _html_to_markdown(rss_content)
        else:
            # Try to scrape full content from the article URL
            try:
                scraped = await _scrape_url(link)
                if scraped:
                    body_md = scraped.get("body_markdown", "")
                    body_html = scraped.get("body_html", "")
            except Exception:
                pass

        # Fallback: use RSS summary if scraping produced nothing
        if not body_md and rss_content:
            body_md = _html_to_markdown(rss_content)
            body_html = rss_content

        word_count = len(body_md) if body_md else 0

        entries.append({
            "title": entry.get("title", ""),
            "link": link,
            "body_markdown": body_md,
            "body_html": body_html,
            "summary": (body_md[:500] if body_md else
                       (entry.get("summary", "")[:500] if entry.get("summary") else "")),
            "cover_image_url": "",
            "published_at": pub_dt,
            "word_count": word_count,
        })

    logger.info("Fetched %d entries from RSS feed", len(entries))
    return entries


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown, optimized for Chinese content."""
    import html2text

    converter = html2text.HTML2Text()
    converter.body_width = 0           # 不自动换行（中文换行会乱）
    converter.unicode_snob = True      # 保留 Unicode 字符
    converter.escape_snob = False      # 不转义特殊字符
    converter.ignore_links = False     # 保留链接
    converter.ignore_images = False    # 保留图片
    converter.ignore_emphasis = False  # 保留强调
    converter.ignore_tables = False    # 保留表格
    converter.protect_links = True     # 链接不被截断
    converter.skip_internal_links = False
    converter.single_line_break = False  # 段落间保留空行（否则图片挤在一起）
    converter.mark_code = True         # 标记代码块
    converter.decode_errors = "replace"

    # 预处理：清理明显非内容的 HTML
    import re as _re
    html = _re.sub(r'<script[^>]*>.*?</script>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r'<style[^>]*>.*?</style>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r'<header[^>]*>.*?</header>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r'<!--.*?-->', '', html, flags=_re.DOTALL)

    md = converter.handle(html)
    # 清理过多空行
    md = _re.sub(r'\n{4,}', '\n\n', md)
    # 清理 [code] [/code] 等 artifact 标签
    md = _re.sub(r'\[/?code\]', '', md)
    return md.strip()


async def _fetch_single_url(url: str) -> Optional[dict]:
    """Fetch and extract a single article from a URL."""
    logger.info("Fetching single URL: %s", url)
    return await _scrape_url(url)


async def _scrape_url(url: str) -> Optional[dict]:
    """Extract article content from a URL.

    Special handling for WeChat official account articles (mp.weixin.qq.com):
    converts lazy-loaded ``data-src`` to ``src`` so images are captured.

    Falls back to readability for non-WeChat URLs.
    """
    validate_url(url)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=_headers())
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to fetch URL %s: %s", url, exc)
            return None

        html = resp.text

        # Pre-process: remove scripts/styles, fix lazy-loaded images
        cleaned = _preprocess_html(html)

        is_wechat = "mp.weixin.qq.com" in url
        summary = ""
        title = ""

        found_js_content = False
        if is_wechat:
            summary, title, found_js_content = _extract_wechat_article(cleaned, html)
            logger.info("WeChat article extraction: title='%s', content_len=%d, found_js_content=%s",
                        title[:50] if title else "", len(summary), found_js_content)

        # 纯图文章可能 HTML 内容短（全是 <img>），但只要找到了 js_content 就不回退到 readability
        if (not summary or len(summary.strip()) < 100) and not found_js_content:
            # Fall back to readability
            from readability import Document as ReadabilityDoc
            doc = ReadabilityDoc(cleaned)
            summary = doc.summary() or ""
            if not title:
                title = doc.title() or ""
            logger.info("Readability fallback: title='%s', content_len=%d",
                        title[:50] if title else "", len(summary))

        # Last resort: grab <body>
        if not summary or len(summary.strip()) < 50:
            body_m = re.search(r'<body[^>]*>(.*?)</body>', cleaned, re.DOTALL | re.IGNORECASE)
            if body_m:
                summary = body_m.group(1)

        body_md = _html_to_markdown(summary)

        # Best-effort title
        if not title:
            og = re.search(
                r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE
            )
            if og:
                title = og.group(1)
        if not title:
            t = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
            if t:
                title = t.group(1)

        # Summary: skip entirely — preview shows full content
        summary_text = ""

        return {
            "title": title.strip() if title else "",
            "link": url,
            "body_markdown": body_md.strip(),
            "body_html": summary,
            "summary": summary_text,
            "cover_image_url": _extract_cover_image(html),
            "published_at": None,
            "word_count": len(body_md.strip()),
        }


def _preprocess_html(html: str) -> str:
    """Clean HTML before content extraction."""
    cleaned = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Convert lazy-loaded images (data-src -> src)
    cleaned = re.sub(r'data-src\s*=\s*"([^"]+)"', r'src="\1"', cleaned)
    cleaned = re.sub(r"data-src\s*=\s*'([^']+)'", r"src='\1'", cleaned)
    cleaned = re.sub(r'data-original\s*=\s*"([^"]+)"', r'src="\1"', cleaned)
    # Remove comments
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
    return cleaned


def _extract_wechat_article(cleaned: str, raw_html: str) -> tuple:
    """Extract content and title from a WeChat official account article."""
    from bs4 import BeautifulSoup

    title = ""

    # Extract title from WeChat embedded data
    t = re.search(r'var\s+msg_title\s*=\s*["\']([^"\']+)["\']', raw_html)
    if t:
        title = t.group(1)
    if not title:
        t = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
                      raw_html, re.IGNORECASE)
        if t:
            title = t.group(1)
    if not title:
        t = re.search(r'<title[^>]*>(.*?)</title>', raw_html, re.DOTALL | re.IGNORECASE)
        if t:
            title = t.group(1)

    content = ""
    found_js_content = False

    # Method 1: Try to find js_content / rich_media_content via BeautifulSoup
    soup = BeautifulSoup(cleaned, 'html.parser')
    js_content = soup.find(id='js_content')
    if js_content:
        content = str(js_content.decode_contents())
        found_js_content = True
    else:
        rich_content = soup.find(id='rich_media_content')
        if rich_content:
            content = str(rich_content.decode_contents())
            found_js_content = True

    # Method 2: New-format pure-image article — images are in picture_page_info_list
    if not content:
        idx = raw_html.find('picture_page_info_list:')
        if idx >= 0:
            # Extract only the FIRST cdn_url of each entry (excludes watermark_info)
            section = raw_html[idx:idx+35000]
            urls = re.findall(
                r"(?:\[|},)\s*\n\s*\{\s*\n\s+cdn_url:\s*'(https?://mmbiz\.qpic\.cn[^']+)'",
                section
            )
            if urls:
                content = "\n\n".join(
                    f'<p><img src="{u.replace("http://", "https://")}" '
                    f'referrerpolicy="no-referrer" style="width:100%"></p>'
                    for u in urls
                )
                found_js_content = True

    # Fix remaining data-src in WeChat images
    if content:
        content = re.sub(r'data-src\s*=\s*"([^"]+)"', r'src="\1"', content)

    return content, title, found_js_content


def _headers() -> dict:
    """Return browser-like headers for HTTP requests."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _extract_cover_image(html: str) -> str:
    """Extract the first meaningful image from HTML for use as cover."""
    # Try og:image first
    match = re.search(
        r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE
    )
    if match:
        return match.group(1)
    match = re.search(
        r'<meta\s+content="([^"]+)"\s+property="og:image"', html, re.IGNORECASE
    )
    if match:
        return match.group(1)
    # Fallback to first img tag
    match = re.search(r'<img[^>]+src="([^"]+)"', html)
    if match:
        return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# Style analysis
# ---------------------------------------------------------------------------


STYLE_ANALYSIS_PROMPT = """Analyze the following WeChat article and extract its writing style profile.

Return a JSON object with these fields:
- tone: overall tone (e.g., professional, casual, humorous, academic, warm, aspirational)
- vocabulary_level: "simple", "moderate", or "advanced"
- sentence_structure: "short_sentences", "mixed", or "long_flowing"
- paragraph_length: "short", "medium", or "long"
- formatting_patterns: list of patterns used (e.g., "emoji", "bullet_points", "blockquotes", "numbered_lists", "bold_headers", "images_in_text")
- avg_sentence_length: approximate number of words per sentence
- first_person_usage: true or false
- hook_style: "question", "statistic", "story", "bold_statement", or "curiosity_gap"
- signature_elements: list of unique stylistic signatures

## Article Content:
{content}

Output ONLY valid JSON:"""


async def analyze_source_style(db: Session, source_id: int, tenant_id: int = 0) -> dict:
    """Analyze the writing style of articles from a feed source.

    Uses the LLM to extract a structured style profile from the top 3-5
    unanalyzed articles and updates ``FeedSource.style_profile``.
    """
    query = db.query(FeedSource).filter(FeedSource.id == source_id)
    if tenant_id:
        query = query.filter(FeedSource.tenant_id == tenant_id)
    source = query.first()
    if not source:
        raise ValueError(f"Feed source {source_id} not found (tenant={tenant_id})")

    # Get unanalyzed articles, or fall back to any articles
    articles = (
        db.query(FeedSourceArticle)
        .filter(
            FeedSourceArticle.feed_source_id == source_id,
            FeedSourceArticle.is_analyzed == False,
        )
        .order_by(FeedSourceArticle.id.desc())
        .limit(5)
        .all()
    )

    if not articles:
        articles = (
            db.query(FeedSourceArticle)
            .filter(FeedSourceArticle.feed_source_id == source_id)
            .order_by(FeedSourceArticle.id.desc())
            .limit(5)
            .all()
        )

    if not articles:
        return {"error": "No articles to analyze"}

    # Concatenate article bodies (truncated to avoid token limits)
    combined = "\n\n---\n\n".join(
        a.body_markdown[:2000] for a in articles if a.body_markdown
    )
    if not combined:
        return {"error": "Articles have no content to analyze"}

    # Call LLM for style analysis
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.dashscope_model,
            messages=[
                {"role": "system", "content": "你是一个专业的写作风格分析专家。"},
                {"role": "user", "content": STYLE_ANALYSIS_PROMPT.format(content=combined)},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content or "{}"
        profile = json.loads(raw)

        # Save profile to FeedSource
        source.style_profile = profile
        db.commit()

        # Mark articles as analyzed
        for a in articles:
            a.is_analyzed = True
        db.commit()

        return {
            "source_id": source_id,
            "articles_analyzed": len(articles),
            "profile": profile,
        }

    except Exception as exc:
        logger.error("Style analysis failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Manual article addition
# ---------------------------------------------------------------------------


def add_manual_article(db: Session, source_id: int, tenant_id: int,
                       title: str, body_markdown: str,
                       summary: Optional[str] = None) -> FeedSourceArticle:
    """Manually add an article to a feed source."""
    fa = FeedSourceArticle(
        tenant_id=tenant_id,
        feed_source_id=source_id,
        title=title[:255],
        body_markdown=body_markdown,
        summary=(summary or body_markdown[:500])[:500],
        word_count=len(body_markdown),
        is_analyzed=False,
    )
    db.add(fa)
    db.commit()
    db.refresh(fa)
    logger.info("Added manual article to feed source %d: %s", source_id, title[:60])
    return fa


def list_source_articles(db: Session, source_id: int,
                          analyzed: Optional[bool] = None,
                          page: int = 1, page_size: int = 20) -> List[FeedSourceArticle]:
    """List articles for a feed source with pagination."""
    query = db.query(FeedSourceArticle).filter(
        FeedSourceArticle.feed_source_id == source_id
    )
    if analyzed is not None:
        query = query.filter(FeedSourceArticle.is_analyzed == analyzed)

    return (
        query.order_by(FeedSourceArticle.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
