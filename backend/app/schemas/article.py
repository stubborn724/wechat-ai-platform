"""Pydantic schemas for article generation (ai-passage-creator)."""

from typing import List, Optional

from pydantic import BaseModel


class TitleOption(BaseModel):
    main_title: str
    sub_title: str


class SelectedTitle(BaseModel):
    main_title: str
    sub_title: str


class OutlineSection(BaseModel):
    section: int
    title: str
    points: List[str]


class OutlineResult(BaseModel):
    sections: List[OutlineSection]


class ImageRequirement(BaseModel):
    position: int
    type: str  # cover, section, inline
    section_title: str = ""
    image_source: str  # PEXELS, NANO_BANANA, etc
    keywords: str = ""
    prompt: str = ""
    placeholder_id: str = ""


class ImageResult(BaseModel):
    position: int
    url: str
    method: str
    keywords: str = ""
    section_title: str = ""
    description: str = ""
    placeholder_id: str = ""


class ArticleState(BaseModel):
    task_id: str
    user_id: int = 0
    topic: str
    style: Optional[str] = None
    title: Optional[SelectedTitle] = None
    title_options: List[TitleOption] = []
    user_description: Optional[str] = None
    outline: Optional[OutlineResult] = None
    content: Optional[str] = None
    image_requirements: List[ImageRequirement] = []
    images: List[ImageResult] = []
    full_content: Optional[str] = None
    enabled_image_methods: Optional[List[str]] = None
    error: Optional[str] = None
    # Knowledge base integration
    knowledge_base_ids: Optional[List[int]] = None
    kb_context: Optional[str] = None
    # Imitation engine
    source_feed_id: Optional[int] = None
    feed_article_ids: Optional[List[int]] = None
    reference_articles: Optional[List[str]] = None  # full article content for imitation
    style_profile: Optional[dict] = None
    # Footer template
    footer_template: Optional[str] = None
    # Batch
    article_count: int = 1
    # Local image pre-selection
    selected_image_urls: Optional[List[str]] = None
