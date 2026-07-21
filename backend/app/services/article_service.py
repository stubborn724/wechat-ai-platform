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
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Article], int]:
    """Return a paginated list of articles together with the total count.

    If *user_id* is provided, results are filtered to that user.
    """
    query = db.query(Article)
    if user_id is not None:
        query = query.filter(Article.user_id == user_id)

    total = query.count()
    articles = (
        query.order_by(Article.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return articles, total


def delete_article(db: Session, article_id: int) -> bool:
    """Delete an article by its primary-key *id*. Returns ``True`` if a row
    was actually deleted."""
    article = db.query(Article).filter(Article.id == article_id).first()
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
