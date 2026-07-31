"""Pydantic schemas for article generation (ai-passage-creator)."""

from typing import List, Literal, Optional

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
    image_source: str  # DASHSCOPE, NANO_BANANA, etc
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


# ---------------------------------------------------------------------------
# Layout / Structure Imitation
# ---------------------------------------------------------------------------


class LayoutBlock(BaseModel):
    """A single content block in the reference article's structural sequence."""

    type: Literal[
        "heading", "paragraph", "image", "image_caption",
        "quote", "note", "divider", "list",
    ]
    role: Optional[str] = None  # semantic role: "opening_scene", "feature_explain", etc.
    level: Optional[int] = None  # heading level (1-6)

    length_chars_target: Optional[int] = None
    length_chars_min: Optional[int] = None
    length_chars_max: Optional[int] = None

    count: int = 1  # how many consecutive blocks of this type
    style_pattern: Optional[str] = None  # e.g. "从‘A’到‘B’：C" for headings


class LayoutSection(BaseModel):
    """A section/chapter in the reference article."""

    section_role: str  # "opening", "selling_point", "closing", "note_block"
    blocks: List[LayoutBlock]


class LayoutTemplate(BaseModel):
    """Structural template extracted from a reference article."""

    schema_version: str = "1.0"
    sections: List[LayoutSection]

    ending_style: str = ""  # "summary" | "interaction" | "emotional_summary" | ...
    total_paragraph_count: int = 0
    total_image_count: int = 0

    layout_features: List[str] = []  # e.g. ["double_images_after_sections", "emotional_closing"]


class ArticleLayoutMeta(BaseModel):
    """Wrapper stored in FeedSourceArticle.analysis."""

    schema_version: str = "1.0"
    layout_status: str = "pending"  # pending | processing | completed | failed
    layout_template: Optional[LayoutTemplate] = None
    layout_error: Optional[str] = None

    analysis_meta: dict = {}
    source_content_hash: str = ""
    analyzed_at: Optional[str] = None


class ArticleState(BaseModel):
    task_id: str
    user_id: int = 0
    # 生成型图片需要按租户归档到 MinIO；默认 0 只用于旧任务兼容，新入口必须传入。
    tenant_id: int = 0
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
    # HTML 仿写优先使用原始网页结构；Markdown 仅保留给旧流程兼容。
    reference_html: Optional[str] = None
    # ERP 产品图生图的投喂源只负责文字与 DOM 结构。开启后保留原图片槽位位置，
    # 但不读取参考图片视觉特征，避免背景规则被外部文章图片污染。
    skip_reference_image_understanding: bool = False
    style_profile: Optional[dict] = None
    # Layout template for structure imitation
    layout_template: Optional[LayoutTemplate] = None
    # Structured content blocks (filled by agent3 with template)
    content_blocks: Optional[List[dict]] = None
    # Footer template
    footer_template: Optional[str] = None
    # Batch
    article_count: int = 1
    # Local image pre-selection
    selected_image_urls: Optional[List[str]] = None
    # ERP 定时任务选择的单个产品原图。该字段只作为图生图参考，不会直接插入正文。
    reference_image_url: Optional[str] = None
    # OpenAI 兼容图片编辑通过 multipart 直接上传规范化参考图；COS URL 仅供万相
    # 降级使用。字节字段不会写入 Article 数据库，只存在于单次任务状态中。
    reference_image_bytes: Optional[bytes] = None
    reference_content_type: Optional[str] = None
    # 从当前品牌知识库检索出的视觉约束，供图生图提示词合成使用。
    image_prompt_context: Optional[str] = None
    # ERP 定时任务本次选中的唯一产品名称。标题、正文和图片 Agent 共用该字段，
    # 避免各阶段根据不同关键词猜测产品而导致标题与图片主体不一致。
    product_name: Optional[str] = None
