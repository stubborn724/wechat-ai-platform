"""LangGraph state definition for the article generation pipeline."""

from typing import Any, List, Optional, TypedDict

from app.schemas.article import (
    ImageRequirement,
    ImageResult,
    OutlineResult,
    SelectedTitle,
    TitleOption,
)


class ArticleGenState(TypedDict):
    """文章生成状态 — 驱动 LangGraph 多智能体文章生成工作流。"""

    # --- Input ---
    task_id: str
    user_id: int
    topic: str
    style: Optional[str]
    image_source: str  # "local" or "pexels"

    # --- Title ---
    title_options: List[TitleOption]
    selected_title: Optional[SelectedTitle]
    user_description: Optional[str]

    # --- Outline ---
    outline: Optional[OutlineResult]

    # --- Content ---
    content: Optional[str]

    # --- Image requirements & results ---
    image_requirements: List[ImageRequirement]
    images: List[ImageResult]

    # --- Merged output ---
    full_content: Optional[str]

    # --- Control ---
    enabled_image_methods: Optional[List[str]]
    error: Optional[str]
    db_session: Optional[Any]  # SQLAlchemy session

    # --- Knowledge base ---
    knowledge_base_ids: Optional[List[int]]
    kb_context: Optional[str]

    # --- Imitation engine ---
    source_feed_id: Optional[int]
    style_profile: Optional[dict]

    # --- Footer template ---
    footer_template: Optional[str]

    # --- Batch ---
    article_count: int
