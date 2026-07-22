"""Agent orchestration service for the article generation pipeline.

Adapted from ai-passage-creator's ``ArticleAgentService``.

Pipeline
--------
1. Generate title options  (agent1)
2. Generate outline        (agent2, streaming)
3. Generate content        (agent3, streaming)
4. Analyse image needs     (agent4)
5. Generate images         (agent5, parallel)
6. Merge images into text  (helper)
"""

import json
import re
from typing import Any, Callable, Dict, List, Optional

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.constants.prompt import (
    AGENT1_TITLE_PROMPT,
    AGENT2_OUTLINE_PROMPT,
    AGENT3_CONTENT_PROMPT,
    AGENT4_IMAGE_REQUIREMENTS_PROMPT,
    AGENT5_IMAGE_EXECUTION_PROMPT,
    get_style_prompt,
)
from app.schemas.article import ArticleState, ImageRequirement, ImageResult, TitleOption

# ---------------------------------------------------------------------------
# DashScope-compatible OpenAI client
# ---------------------------------------------------------------------------

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = settings.dashscope_model
STREAM_CHUNK_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> dict:
    """Extract the first JSON object from ``text``.

    Handles both raw JSON and markdown-fenced blocks (`` ```json ... ``` ``).
    """
    # Try to extract a fenced JSON block first
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    # If still not pure JSON, look for the first { ... } spanning the text
    if not text.startswith("{"):
        brace_start = text.find("{")
        if brace_start != -1:
            text = text[brace_start:]
        brace_end = text.rfind("}")
        if brace_end != -1:
            text = text[: brace_end + 1]

    return json.loads(text)


def _build_outline_text(state: ArticleState) -> str:
    """Convert the article outline into a human-readable string for prompts."""
    if not state.outline:
        return ""
    lines = []
    for section in state.outline.sections:
        lines.append(f"## {section.title}")
        for point in section.points:
            lines.append(f"- {point}")
    return "\n".join(lines)


async def _call_llm(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.8,
) -> str:
    """Call the LLM with a system and user message, returning the full text
    response."""
    client = _get_client()
    response = await client.chat.completions.create(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
        stream=False,
    )
    return response.choices[0].message.content or ""


async def _call_llm_with_streaming(
    system_prompt: str,
    user_message: str,
    stream_handler: Callable[[str], None],
    model: Optional[str] = None,
    temperature: float = 0.8,
) -> str:
    """Call the LLM with streaming, passing each text delta to
    *stream_handler*.

    Returns the fully assembled response string.
    """
    client = _get_client()
    full_content: List[str] = []

    stream = await client.chat.completions.create(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            full_content.append(delta)
            stream_handler(delta)

    return "".join(full_content)


# ---------------------------------------------------------------------------
# Agent steps
# ---------------------------------------------------------------------------


async def agent1_generate_title_options(state: ArticleState) -> ArticleState:
    """Call the LLM to generate 6 title options for the given topic/style."""
    if state.title_options:
        # Already have titles — skip
        return state

    style_prompt = get_style_prompt(state.style or "")
    prompt = AGENT1_TITLE_PROMPT.format(
        topic=state.topic,
        style=state.style or "default",
    )
    if style_prompt:
        prompt += f"\n\n{style_prompt}"

    # Inject style profile for imitation mode
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)

    # Inject reference articles for imitation (full content)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)

    system_msg = "你是一个专业的微信公众号标题生成专家。所有输出必须使用纯中文，禁止任何英文单词或中英混合。"
    if state.style_profile:
        system_msg += " 请严格按照提供的仿写风格指南来生成标题方案。"

    raw = await _call_llm(
        system_msg,
        prompt,
    )

    try:
        data = _parse_json_response(raw)
        options = data.get("title_options", [])
        state.title_options = [TitleOption(**opt) for opt in options]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        state.error = f"Failed to parse title options: {exc}"

    return state


async def agent2_generate_outline(
    state: ArticleState,
    stream_handler: Optional[Callable[[str], None]] = None,
) -> ArticleState:
    """Stream an article outline from the LLM based on the selected title."""
    if not state.title:
        state.error = "No title selected before outline generation"
        return state

    style_section = get_style_prompt(state.style or "")
    prompt = AGENT2_OUTLINE_PROMPT.format(
        topic=state.topic,
        main_title=state.title.main_title,
        sub_title=state.title.sub_title,
        style=state.style or "default",
        user_description=state.user_description or "无",
        style_section=style_section,
    )

    if state.user_description:
        prompt += f"\n\n## 用户补充说明\n{state.user_description}"

    # Inject style profile for imitation mode
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)

    # Inject reference articles for imitation (full content)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)

    def _noop_handler(text: str) -> None:
        pass

    handler = stream_handler or _noop_handler
    raw = await _call_llm_with_streaming(
        "你是一个专业的内容策划专家。",
        prompt,
        handler,
    )

    try:
        data = _parse_json_response(raw)
        from app.schemas.article import OutlineResult

        state.outline = OutlineResult(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        state.error = f"Failed to parse outline: {exc}"

    return state


async def agent3_generate_content(
    state: ArticleState,
    stream_handler: Optional[Callable[[str], None]] = None,
) -> ArticleState:
    """Stream the full article content from the LLM."""
    if not state.title or not state.outline:
        state.error = "Title and outline are required before content generation"
        return state

    style_section = get_style_prompt(state.style or "")
    outline_text = _build_outline_text(state)

    prompt = AGENT3_CONTENT_PROMPT.format(
        main_title=state.title.main_title,
        sub_title=state.title.sub_title,
        style=state.style or "default",
        outline_text=outline_text,
        style_section=style_section,
    )

    # Inject knowledge base context if available
    system_msg = (
        "你是一个专业的微信公众号文章写手。全文必须使用纯中文写作。\n"
        "图片很重要！在文中适当位置插入图片标记：`[IMAGE:position=N,keywords=中文描述,type=T]`，"
        "每篇文章必须包含4～8张配图标记。keywords写图片展示的内容即可。\n"
        "正文中不得出现摄影术语（如俯拍、特写、暖光、45度等），不得虚构品牌价格联系方式。"
    )
    if state.kb_context:
        prompt += (
            f"\n\n## 参考资料（请基于以下参考资料来撰写文章内容，确保信息准确）\n"
            f"{state.kb_context}\n"
        )
        system_msg = (
            "你是一个专业的微信公众号文章写手。"
            "你有参考资料可供使用，请确保文章内容与参考资料中的事实一致，"
            "并在适当位置引用参考信息。"
        )

    # Inject style profile for imitation mode
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)

    # Inject reference articles for imitation (full content)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)
        # Override system message: when imitating, follow reference format
        system_msg = (
            "你是一个专业的仿写专家。你正在仿写一篇公众号文章。\n"
            "在文中适当位置插入图片标记：`[IMAGE:position=N,keywords=中文描述,type=T]`\n"
            "图片很重要，每篇文章至少包含4张配图，请确保插入了足够数量的[IMAGE:]标记。\n"
            "keywords 写图片展示的内容即可（如「客厅全景」「教师办公场景」「产品细节」），不要写拍摄角度或光线。\n"
            "正文中不得出现任何摄影术语（俯拍、仰拍、特写、微距、暖光、逆光、45度等）。\n"
            "【重要】标题必须用 **加粗** 包裹\n"
            "【重要】禁止使用 > 引用块格式\n"
            "【重要】禁止重复输出同一个标题或总结句\n"
            "【重要】全文必须使用纯中文写作\n"
        )

    def _noop_handler(text: str) -> None:
        pass

    handler = stream_handler or _noop_handler
    content = await _call_llm_with_streaming(
        system_msg,
        prompt,
        handler,
    )

    state.content = content
    return state


async def agent4_analyze_image_requirements(state: ArticleState) -> ArticleState:
    """Analyse the generated content and determine where images are needed.

    Returns the state with ``image_requirements`` populated.
    """
    if not state.content:
        state.error = "Content is required before image requirement analysis"
        return state

    enabled_methods = state.enabled_image_methods or ["PEXELS"]
    enabled_methods_text = ", ".join(enabled_methods)

    main_title = (
        state.title.main_title if state.title else state.topic
    )
    prompt = AGENT4_IMAGE_REQUIREMENTS_PROMPT.format(
        main_title=main_title,
        content=state.content[:8000],  # truncate to avoid token limits
        enabled_methods_text=enabled_methods_text,
    )

    raw = await _call_llm(
        "你是一个专业的图片编辑专家。",
        prompt,
        temperature=0.5,
    )

    try:
        data = _parse_json_response(raw)
        requirements = data.get("image_requirements", [])
        state.image_requirements = [ImageRequirement(**req) for req in requirements]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        state.error = f"Failed to parse image requirements: {exc}"

    return state


async def agent5_generate_images(
    state: ArticleState,
    stream_handler: Optional[Callable[[str], None]] = None,
) -> ArticleState:
    """Execute image searches for each requirement in parallel.

    This agent dispatches calls to the appropriate image service for each
    requirement and collects the results.

    If ``state.selected_image_urls`` is provided (user pre-selected local images),
    those are used directly instead of searching external sources.
    """
    if not state.image_requirements:
        state.error = "No image requirements to process"
        return state

    # ---- User pre-selected local images ----
    if state.selected_image_urls:
        results: List[ImageResult] = []
        for i, req in enumerate(state.image_requirements):
            url = state.selected_image_urls[i] if i < len(state.selected_image_urls) else ""
            results.append(
                ImageResult(
                    position=req.position,
                    url=url,
                    method="local",
                    keywords=req.keywords or "",
                    section_title=req.section_title,
                    placeholder_id=req.placeholder_id,
                )
            )
            if stream_handler:
                stream_handler(f"[Image {req.position}/{len(state.image_requirements)}: local - {url}]\n")
        state.images = results
        return state

    # ---- Default: search external sources ----
    from app.services.image_service_v2 import ImageServiceStrategy

    strategy = ImageServiceStrategy()
    results: List[ImageResult] = []

    for req in state.image_requirements:
        method = req.image_source or "PEXELS"
        keywords = req.keywords or req.section_title or ""

        url = await strategy.execute(method, keywords, prompt=req.prompt)

        results.append(
            ImageResult(
                position=req.position,
                url=url or "",
                method=method,
                keywords=keywords,
                section_title=req.section_title,
                placeholder_id=req.placeholder_id,
            )
        )

        if stream_handler:
            stream_handler(f"[Image {req.position}/{len(state.image_requirements)}: {method} - {url}]\n")

    state.images = results
    return state


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def merge_images_into_content(state: ArticleState) -> ArticleState:
    """Replace ``[IMAGE:...]`` placeholders in ``state.content`` with
    Markdown image syntax using the fetched image URLs.

    Placeholder format::

        [IMAGE:position=N,keywords=...]

    Then runs a post-processing pass to:
    - Remove any unreplaced ``[IMAGE:...]`` placeholders
    - Normalize excessive blank lines (4+ → 2)

    Also appends the footer template (if configured).

    This also populates ``state.full_content``.
    """
    content = state.content or ""

    if state.images:
        # Build a lookup by position
        images_by_position: Dict[int, str] = {}
        for img in state.images:
            images_by_position[img.position] = img.url

        def _replace_placeholder(match: re.Match) -> str:
            raw = match.group(1)
            pos_match = re.search(r"position=(\d+)", raw)
            pos = int(pos_match.group(1)) if pos_match else 0

            kw_match = re.search(r"keywords=([^,\]]+)", raw)
            alt = kw_match.group(1).strip() if kw_match else "image"

            url = images_by_position.get(pos, "")
            if url:
                # Output image — description cleaned by post-processing if needed
                return f"{alt}\n\n![{alt}]({url})"
            # No image available — remove placeholder entirely
            return ""

        content = re.sub(r"\[IMAGE:(.*?)\]", _replace_placeholder, content)

    # Post-processing: remove any remaining [IMAGE:] placeholders
    # (in case the images list was empty or placeholders were missed)
    remaining = re.findall(r'\[IMAGE:[^\]]*\]', content)
    if remaining:
        logger.warning("Found %d unreplaced [IMAGE:] placeholders — removing them", len(remaining))
    content = re.sub(r'\[IMAGE:[^\]]*\]', '', content)

    # Post-processing: normalize excessive blank lines (4+ → 2)
    # This prevents the "poetry-like" formatting with too many line breaks
    content = re.sub(r'\n{4,}', '\n\n', content)

    # Post-processing: normalize 3+ spaces at line starts
    content = re.sub(r'^ {3,}', '', content, flags=re.MULTILINE)

    # Append footer template
    if state.footer_template:
        footer = state.footer_template.strip()
        if footer:
            content = f"{content}\n\n---\n\n{footer}"

    state.full_content = content
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_style_profile_section(profile: dict) -> str:
    """Build a style guide section from a style profile dict for prompt injection."""
    # Map English-style profile values to Chinese descriptions
    tone_map = {
        "warm": "温暖亲切", "professional": "专业严谨", "casual": "轻松随意",
        "humorous": "幽默风趣", "academic": "学术正式", "aspirational": "励志向上",
    }
    level_map = {
        "simple": "简单易懂", "moderate": "适中", "advanced": "较高",
    }
    struct_map = {
        "short_sentences": "短句为主", "mixed": "长短句结合", "long_flowing": "长句流畅",
    }
    length_map = {
        "short": "短小精悍", "medium": "适中", "long": "较长",
    }
    hook_map = {
        "question": "提问式开头", "statistic": "数据式开头", "story": "故事式开头",
        "bold_statement": "观点式开头", "curiosity_gap": "悬念式开头",
    }

    lines = ["\n\n## 仿写风格指南（只模仿以下风格特征，内容围绕用户主题重新创作）"]

    tone = profile.get("tone")
    if tone:
        lines.append(f"- 语气：{tone_map.get(tone.lower(), tone)}")

    level = profile.get("vocabulary_level")
    if level:
        lines.append(f"- 词汇难度：{level_map.get(level.lower(), level)}")

    structure = profile.get("sentence_structure")
    if structure:
        lines.append(f"- 句子结构：{struct_map.get(structure.lower(), structure)}")

    plen = profile.get("paragraph_length")
    if plen:
        lines.append(f"- 段落长度：{length_map.get(plen.lower(), plen)}")

    hook = profile.get("hook_style")
    if hook:
        lines.append(f"- 开头风格：{hook_map.get(hook.lower(), hook)}")

    formatting = profile.get("formatting_patterns")
    if formatting:
        fmt_cn = []
        for f in formatting:
            m = {"emoji": "使用表情", "bullet_points": "使用要点列表",
                 "blockquotes": "使用引用块", "numbered_lists": "使用编号列表",
                 "bold_headers": "加粗标题", "images_in_text": "文中配图"}
            mapped = m.get(f.lower(), f)
            # Skip formatting items that are about image descriptions
            if any(kw in mapped for kw in ['图片', '图像', '摄影', '拍摄']):
                continue
            fmt_cn.append(mapped)
        if fmt_cn:
            lines.append(f"- 格式特征：{', '.join(fmt_cn)}")

    signatures = profile.get("signature_elements")
    if signatures:
        # Filter out any signature elements related to image/photography descriptions
        photography_keywords = ['图片', '图像', '摄影', '拍摄', '配图', '插图', '照片', '产品图', '场景图']
        filtered = [s for s in signatures if not any(kw in s for kw in photography_keywords)]
        if filtered:
            lines.append(f"- 独特标志：{', '.join(filtered)}")

    return "\n".join(lines)


def _build_reference_articles_section(articles: list) -> str:
    """Build a reference articles section for prompt injection.

    Only passes short excerpts (style sample only) to avoid the LLM
    reproducing product/image descriptions from the source articles.
    """
    if not articles:
        return ""

    lines = ["\n\n## 参考文章风格摘要（仅展示句式结构和段落节奏，不要复制具体内容）"]
    for i, article_text in enumerate(articles, start=1):
        # Strip [IMAGE:] markers and common photography lines first
        cleaned = re.sub(r'\[IMAGE:[^\]]*\]', '', article_text)
        cleaned = re.sub(
            r'^.*?(?:45度|俯拍|仰拍|微距|特写|暖光|逆光|打光|布光).*?(?:场景|效果|展示|组合|特写).*?\n',
            '', cleaned, flags=re.MULTILINE,
        )
        cleaned = cleaned.strip()
        # Only keep first 300 chars — enough for style, not enough for content
        excerpt = cleaned[:300]
        if len(cleaned) > 300:
            excerpt += "\n\n...（风格摘要）"
        if excerpt and len(excerpt) > 50:
            lines.append(f"\n### 风格示例 {i}\n{excerpt}")

    lines.append("""

## ⚠️ 输出格式规则

### 核心要求：
1. **标题加粗**：所有小标题/段落总结语/独立成行的主题句，用 `**加粗**` 包裹
2. **完整段落**：每一段必须是多句话连贯而成的完整段落
3. **段落间距**：段与段之间空一行
4. **图片标记（必须）**：每篇文章必须插入4～8张配图标记 `[IMAGE:position=N,keywords=图片内容描述,type=T]`
   - keywords 只写图片内容（如「客厅全景」「教师办公」「产品细节」），不得包含任何摄影术语
5. **禁止使用**：禁止使用 `>` 引用块、`---` 分隔线、`***`

### 【绝对禁止】以下内容不得出现在正文中：
- 拍摄角度、光线、构图等任何图片描述文字
- 具体产品名、品牌名、价格、联系方式
- 参考文章中的专属名词和特定产品描述
- 摄影术语：俯拍、仰拍、特写、微距、暖光、逆光、45度、打光、布光、景深、背景虚化

### 重要：
- 你的文章内容必须围绕用户给定的**主题**来写
- 只模仿句子的**长短节奏**和**段落结构**，不复制具体写什么
""")

    return "\n".join(lines)
