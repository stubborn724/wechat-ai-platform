"""Article CRUD service — business logic between API routes and the Article model."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.mysql_models import Article


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_article(
    db: Session,
    user_id: int,
    tenant_id: int,
    topic: str,
    style: str,
    image_source: Optional[str] = None,
    footer_template: Optional[str] = None,
) -> Article:
    """Create a new article record and persist it immediately.

    Returns the newly created :class:`Article` instance.
    """
    article = Article(
        task_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        topic=topic,
        style=style,
        phase="pending",
        status="pending",
        footer_template=footer_template,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def get_article(db: Session, task_id: str) -> Optional[Article]:
    """Fetch an article by its unique *task_id*."""
    return db.query(Article).filter(Article.task_id == task_id).first()


def list_articles(
    db: Session,
    user_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Article], int]:
    """Return a paginated list of articles together with the total count.

    If *user_id* is provided, results are filtered to that user.
    If *tenant_id* is provided, results are filtered to that tenant.
    """
    query = db.query(Article)
    if user_id is not None:
        query = query.filter(Article.user_id == user_id)
    if tenant_id is not None:
        query = query.filter(Article.tenant_id == tenant_id)

    total = query.count()
    articles = (
        query.order_by(Article.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return articles, total


def get_article_by_id(db: Session, article_id: int, tenant_id: Optional[int] = None) -> Optional[Article]:
    """Fetch an article by its primary-key id, optionally scoped to tenant."""
    query = db.query(Article).filter(Article.id == article_id)
    if tenant_id is not None:
        query = query.filter(Article.tenant_id == tenant_id)
    return query.first()


def delete_article(db: Session, article_id: int, tenant_id: Optional[int] = None) -> bool:
    """Delete an article by its primary-key *id*. Returns ``True`` if a row
    was actually deleted."""
    query = db.query(Article).filter(Article.id == article_id)
    if tenant_id is not None:
        query = query.filter(Article.tenant_id == tenant_id)
    article = query.first()
    if article is None:
        return False
    db.delete(article)
    db.commit()
    return True


def update_article_phase(
    db: Session,
    task_id: str,
    phase: str,
    **updates: Any,
) -> Optional[Article]:
    """Update the article's *phase* and any additional keyword fields.

    Supported keyword fields include: ``status``, ``error_message``,
    ``main_title``, ``sub_title``, ``title_options``, ``outline``, ``content``,
    ``full_content``, ``images``, ``cover_image``.
    """
    article = get_article(db, task_id)
    if article is None:
        return None

    article.phase = phase
    for key, value in updates.items():
        if hasattr(article, key):
            setattr(article, key, value)

    db.commit()
    db.refresh(article)
    return article


def save_title_options(db: Session, task_id: str, title_options: list) -> Optional[Article]:
    """Persist the list of title option dicts for the given article."""
    return update_article_phase(db, task_id, phase="title_generated", title_options=title_options)


def save_outline(db: Session, task_id: str, outline: dict) -> Optional[Article]:
    """Persist the outline dict for the given article."""
    return update_article_phase(db, task_id, phase="outline_generated", outline=outline)


def save_content(
    db: Session,
    task_id: str,
    content: str,
    full_content: str,
    images: Optional[List[Dict[str, Any]]] = None,
    cover_image: Optional[str] = None,
    footer_template: Optional[str] = None,
) -> Optional[Article]:
    """Persist the generated content, raw content, images metadata and cover
    image URL in a single call."""
    # Final safety net: strip any photography/image description text
    import re as _re
    content = _strip_photography_lines(content)
    full_content = _strip_photography_lines(full_content)
    return update_article_phase(
        db,
        task_id,
        phase="content_generated",
        content=content,
        full_content=full_content,
        images=images or [],
        cover_image=cover_image or "",
        footer_template=footer_template or "",
    )


def _strip_photography_lines(text: str) -> str:
    """Final safety net: remove lines containing photography/image description language.

    Strategy:
    1. Extract all IMAGE keywords (AI's own image descriptions) → remove matching body text
    2. Remove lines with 2+ photography terms
    3. Remove [IMAGE:] markers
    """
    if not text:
        return text
    import re as _re

    # Step 1: Extract all image keywords from [IMAGE:keywords=XXX] markers AND markdown alt text
    image_keywords = _re.findall(r'keywords=([^,\]]+)', text)
    alt_texts = _re.findall(r'!\[([^\]]+)\]\([^)]+\)', text)
    image_keywords.extend(alt_texts)
    # Strip [IMAGE:] markers first
    text = _re.sub(r'\[IMAGE:[^\]]*\]', '', text)

    lines = text.split("\n")
    photo_keywords = [
        '俯拍', '仰拍', '侧拍', '微距', '特写', '近景', '远景', '中景',
        '暖光', '逆光', '侧光', '顶光', '底光', '打光', '布光',
        '景深', '光圈', '快门', '45度',
    ]

    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            cleaned.append(line)
            continue

        # ALWAYS preserve markdown image lines AND HTML img tags
        if _re.match(r'^!\[.*\]\(.*\)$', s) or _re.match(r'^<img\s+[^>]+/?>$', s, _re.IGNORECASE):
            cleaned.append(line)
            continue

        # Step 2: Remove if line matches any image keyword phrase (AI wrote it as description)
        if image_keywords:
            is_image_desc = False
            for kw in image_keywords:
                # If the line contains a significant portion of the keyword phrase
                if len(kw) >= 6 and kw in s:
                    is_image_desc = True
                    break
                # Partial match: if >60% of keyword chars appear in the line in order
                if len(kw) >= 10:
                    common = sum(1 for c in kw if c in s)
                    if common / len(kw) > 0.6:
                        is_image_desc = True
                        break
            if is_image_desc:
                continue

        # Step 3: Skip if line starts with degree number (e.g. "45度俯拍...")
        if _re.match(r'^\d+度', s):
            continue
        # Step 4: Skip if line has 2+ photography keywords
        count = sum(1 for kw in photo_keywords if kw in s)
        if count >= 2:
            continue
        # Step 5: Skip watermark/corner marks
        if _re.search(r'(?:右下角|左下角|右上角|左上角).*(?:水印|文字|标志|logo)', s, _re.IGNORECASE):
            continue

        cleaned.append(line)

    return "\n".join(cleaned)
