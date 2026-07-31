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

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.constants.prompt import (
    AGENT1_IMITATION_TITLE_PROMPT,
    AGENT1_TITLE_PROMPT,
    AGENT2_OUTLINE_PROMPT,
    AGENT3_CONTENT_PROMPT,
    AGENT4_IMAGE_REQUIREMENTS_PROMPT,
    AGENT5_IMAGE_EXECUTION_PROMPT,
    get_style_prompt,
)
from app.schemas.article import ArticleState, ImageRequirement, ImageResult, TitleOption


# 本模块同时被接口请求与 Celery 定时任务调用，使用标准日志器可确保图片成本限制、
# 降级和异常信息进入各自进程的控制台日志，且不会把排障信息暴露到前端响应中。
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STREAM_CHUNK_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> dict:
    """Extract JSON from ``text``.

    Handles markdown-fenced blocks, both object ``{...}`` and array ``[...]``
    top-level values, and pure JSON strings.
    """
    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    # Try parsing as-is first (fast path)
    text = text.strip()
    try:
        result = json.loads(text)
        # Normalise array → object with "sections" key
        if isinstance(result, list):
            return {"sections": result}
        return result
    except json.JSONDecodeError:
        pass

    # Fallback: find the outermost { ... } or [ ... ]
    if text.startswith("{"):
        brace_start, brace_end = 0, text.rfind("}")
    elif text.startswith("["):
        # Array: find matching brackets
        brace_start = 0
        depth = 0
        brace_end = -1
        for i, ch in enumerate(text):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    brace_end = i + 1
                    break
        if brace_end == -1:
            raise json.JSONDecodeError("Unmatched brackets", text, 0)
    else:
        brace_start = text.find("{")
        if brace_start == -1:
            brace_start = text.find("[")
        if brace_start == -1:
            raise json.JSONDecodeError("No JSON found", text, 0)
        if text[brace_start] == "{":
            brace_end = text.rfind("}") + 1
        else:
            depth = 0
            brace_end = -1
            for i in range(brace_start, len(text)):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        brace_end = i + 1
                        break
            if brace_end == -1:
                raise json.JSONDecodeError("Unmatched brackets", text, 0)

    text = text[brace_start:brace_end]
    result = json.loads(text)
    if isinstance(result, list):
        return {"sections": result}
    return result


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


def _append_product_title_requirement(prompt: str, product_name: str | None) -> str:
    """向不同标题 Agent 追加统一的 ERP 产品名硬约束。

    定时任务既可能走通用标题生成，也可能走专用仿写标题生成；使用同一合成函数
    可以避免只修改其中一个入口，导致实际发布标题再次遗漏产品名称。
    """

    normalized_name = str(product_name or "").strip()
    if not normalized_name:
        return prompt
    return prompt + (
        "\n\n## ERP 目标产品\n"
        f"产品名称：{normalized_name}\n"
        "每一组主标题都必须原样包含以上完整产品名称，并围绕该产品创作。"
    )


async def _call_llm(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.8,
) -> str:
    """通过统一文生文路由返回完整结果，主站失败时自动切换百炼。"""
    from app.services.text_generation_service import (
        TextGenerationRequest,
        text_generation_service,
    )

    return await text_generation_service.complete(TextGenerationRequest(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        model_override=model,
    ))


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
    from app.services.text_generation_service import (
        TextGenerationRequest,
        text_generation_service,
    )

    return await text_generation_service.stream(
        TextGenerationRequest(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            model_override=model,
        ),
        stream_handler,
    )


# ---------------------------------------------------------------------------
# Agent steps
# ---------------------------------------------------------------------------


async def agent1_generate_title_options(state: ArticleState) -> ArticleState:
    print(f"  ▶ agent1: 生成标题方案...")
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
    # HTML 仿写已经以 DOM 槽位锁定格式；标题只需产品与风格信息，不再携带整篇
    # 投喂原文，避免为六个候选标题重复消耗数千 token。
    if state.reference_articles and not state.reference_html:
        prompt += _build_reference_articles_section(state.reference_articles)
    # 通用标题入口同样用于 ERP 定时任务，必须在调用模型前注入真实产品名。
    prompt = _append_product_title_requirement(prompt, state.product_name)

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


async def agent1_generate_imitation_title(
    state: ArticleState,
    reference_title: str,
) -> ArticleState:
    """根据投喂文章生成一个原创仿写标题。

    此 Agent 专门服务于“已选择投喂文章但未填写主题”的自动流程。参考标题和
    参考正文用于分析原文主题及表达风格，模型只负责给出新标题；调用方随后把
    新标题作为大纲和正文 Agent 的主题，确保全篇围绕同一个仿写方向生成。

    参考原标题绝不能作为失败兜底。若模型返回空标题或复用原题，函数写入
    ``state.error``，由上层停止生成，避免将投喂文章原题误发布为新文章。
    """
    normalized_reference_title = (reference_title or "").strip()
    if not normalized_reference_title:
        state.error = "标题仿写失败：投喂文章缺少可用标题"
        return state

    style_prompt = get_style_prompt(state.style or "")
    prompt = AGENT1_IMITATION_TITLE_PROMPT.format(
        reference_title=normalized_reference_title,
    )
    if style_prompt:
        prompt += f"\n\n{style_prompt}"
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)

    # 专用仿写入口和通用标题入口共用同一产品名规则，保证后续切换 Agent 时行为一致。
    prompt = _append_product_title_requirement(prompt, state.product_name)

    raw = await _call_llm(
        "你是严谨的微信公众号标题仿写 Agent。只返回一个包含原创标题的 JSON 对象。",
        prompt,
        temperature=0.7,
    )

    try:
        data = _parse_json_response(raw)
        raw_options = data.get("title_options", [])
        valid_options = []
        for raw_option in raw_options:
            option = TitleOption(**raw_option)
            if not option.main_title.strip():
                continue
            if _is_same_title(option.main_title, normalized_reference_title):
                continue
            valid_options.append(option)

        if not valid_options:
            state.error = "标题仿写失败：生成标题不能为空，且不能与参考原标题相同"
            state.title_options = []
            return state

        state.title_options = valid_options
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        state.error = f"标题仿写失败：模型返回格式无效：{exc}"
        state.title_options = []

    return state


def _is_same_title(candidate_title: str, reference_title: str) -> bool:
    """比较标题是否只是参考原题的标点或空白变体。

    仅靠字符串全等会放过“原标题！”这类伪仿写结果。这里移除空白和标点后
    再比较，允许真实改写保留相同主题，但拒绝不具有原创性的原题复用。
    """
    def normalize(title: str) -> str:
        return re.sub(r"[\W_]", "", title, flags=re.UNICODE).casefold()

    return normalize(candidate_title) == normalize(reference_title)


async def agent2_generate_outline(
    state: ArticleState,
    stream_handler: Optional[Callable[[str], None]] = None,
) -> ArticleState:
    print(f"  ▶ agent2: 生成大纲...")
    """Stream an article outline from the LLM based on the selected title."""
    if not state.title:
        state.error = "No title selected before outline generation"
        return state

    style_section = get_style_prompt(state.style or "")
    section_count = (
        str(len(state.layout_template.sections))
        if state.layout_template and state.layout_template.sections
        else "4-6"
    )
    prompt = AGENT2_OUTLINE_PROMPT.format(
        topic=state.topic,
        main_title=state.title.main_title,
        sub_title=state.title.sub_title,
        style=state.style or "default",
        user_description=state.user_description or "无",
        style_section=style_section,
        section_count=section_count,
    )

    if state.user_description:
        prompt += f"\n\n## 用户补充说明\n{state.user_description}"

    # Inject style profile for imitation mode
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)

    # Inject reference articles for imitation (full content)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)

    # Inject layout template constraints (structure imitation)
    if state.layout_template:
        prompt += _build_layout_section(state)

    def _noop_handler(text: str) -> None:
        pass

    system_msg = "你是一个专业的内容策划专家。"
    if state.layout_template:
        system_msg += (
            " 你有一份「版式结构约束」需要严格遵循。"
            "大纲的章节数量、顺序必须与约束完全一致。"
            "章节标题需围绕用户主题重新创作，不得照搬参考文章标题。"
        )

    handler = stream_handler or _noop_handler
    raw = await _call_llm_with_streaming(
        system_msg,
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
    print(f"  ▶ agent3: 生成正文（最多等 120 秒）...")
    """Generate article content.

    Two modes:
    1. Layout template present → structured JSON output (LLM fills blocks)
    2. No template → free-form Markdown output (original behavior)
    """
    if not state.title:
        state.error = "Title is required before content generation"
        return state

    # 投喂仿写存在原始 HTML 时，优先锁定真实 DOM 结构。这个分支必须先于旧的
    # LayoutTemplate/Markdown 流程执行，避免已经拿到的网页样式再次被压缩成文本块。
    if state.reference_html:
        return await agent3_generate_html_imitation_content(state)

    if not state.outline:
        state.error = "Outline is required before non-HTML content generation"
        return state

    # ========================================================================
    # Mode 1: Layout template → structured block filling
    # ========================================================================
    if state.layout_template and state.layout_template.sections:
        return await _generate_structured_content(state, stream_handler)

    # ========================================================================
    # Mode 2: No template → free-form Markdown (original behavior)
    # ========================================================================
    style_section = get_style_prompt(state.style or "")
    outline_text = _build_outline_text(state)

    prompt = AGENT3_CONTENT_PROMPT.format(
        main_title=state.title.main_title,
        sub_title=state.title.sub_title,
        style=state.style or "default",
        outline_text=outline_text,
        style_section=style_section,
    )

    system_msg = (
        "你是一个专业的微信公众号文章写手。全文必须使用纯中文写作。\n"
        "图片很重要！在文中适当位置插入图片标记：`[IMAGE:position=N,keywords=中文描述,type=T]`，"
        "每篇文章必须包含4～8张配图标记。keywords写图片展示的内容即可。\n"
        "正文中不得出现摄影术语（如俯拍、特写、暖光、45度等），不得虚构品牌价格联系方式。"
    )

    if state.kb_context:
        prompt += f"\n\n## 参考资料（请基于以下参考资料来撰写文章内容，确保信息准确）\n{state.kb_context}\n"
        system_msg = "你是一个专业的微信公众号文章写手。你有参考资料可供使用，请确保文章内容与参考资料中的事实一致，并在适当位置引用参考信息。"

    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)

    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)

    def _noop(text: str) -> None:
        pass

    handler = stream_handler or _noop
    state.content = await _call_llm_with_streaming(system_msg, prompt, handler)
    return state


async def agent3_generate_html_imitation_content(state: ArticleState) -> ArticleState:
    """执行 HTML 主流程的文字与图片内容分析 Agent。

    输入是投喂文章的原始 HTML，而不是 Markdown。函数先由程序构建不可变 DOM
    蓝图，再让模型仅返回各槽位的新文字与图片提示词；随后由结构服务完成回填。
    这样文字 Agent 无法移动节点，图片 Agent 也只能替换原来的 ``img`` 位置。
    """
    from app.agent.nodes.image_understanding_node import understand_images
    from app.services.html_imitation_service import (
        analyze_html_for_imitation,
        render_html_imitation,
        select_html_image_slots,
    )

    try:
        blueprint = analyze_html_for_imitation(state.reference_html or "")
        if state.skip_reference_image_understanding:
            # ERP 产品是唯一真实视觉主体，品牌知识库已经给出目标背景。此处仍保留
            # 图片 DOM 槽位，但不调用参考文章图片理解，防止外部图片风格混入提示词。
            visual_descriptions, excluded_image_slot_ids = {}, set()
        else:
            visual_descriptions, excluded_image_slot_ids = await _understand_reference_images(
                blueprint,
                understand_images,
            )
        generated_image_slot_ids, empty_image_slot_ids = select_html_image_slots(
            blueprint,
            excluded_image_slot_ids=excluded_image_slot_ids,
            # 单个定时任务可提高图片数量；ArticleState 默认值继续提供历史兼容和成本保护。
            max_generated_images=state.max_generated_images,
        )
        non_generated_image_slot_ids = excluded_image_slot_ids | set(empty_image_slot_ids)
        prompt = _build_html_imitation_prompt(
            state,
            blueprint.prompt_payload(
                excluded_image_slot_ids=non_generated_image_slot_ids,
                include_source_urls=not state.skip_reference_image_understanding,
            ),
            visual_descriptions,
        )
        raw = await _call_llm(
            "你是微信公众号 HTML 仿写内容 Agent。你只生成 JSON 槽位内容，绝不输出 HTML、"
            "Markdown、参考原文句子或额外字段。",
            prompt,
            temperature=0.7,
        )
        data = _parse_json_response(raw)
        text_by_slot = _index_agent_slots(data.get("text_slots", []), "content")
        text_by_slot = await _repair_duplicate_article_title_slots(
            state,
            blueprint,
            text_by_slot,
        )
        image_by_slot = _index_agent_slots(data.get("image_slots", []), "keywords")
        image_by_slot = _compose_html_image_slot_prompts(
            image_by_slot,
            visual_descriptions,
            non_generated_image_slot_ids,
        )
        rendered = render_html_imitation(
            blueprint,
            text_by_slot=text_by_slot,
            image_by_slot=image_by_slot,
            excluded_image_slot_ids=excluded_image_slot_ids,
            empty_image_slot_ids=set(empty_image_slot_ids),
            footer_template=state.footer_template or "",
        )
        state.content = rendered.html
        state.image_requirements = list(rendered.image_requirements)
        return state
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        state.error = f"HTML imitation content generation failed: {exc}"
        return state


async def _understand_reference_images(
    blueprint,
    understand_images: Callable[[List[str]], List[dict]],
) -> tuple[dict, set[str]]:
    """调用统一参考图片分析，并返回视觉特征和二维码槽位集合。

    图片理解是网络模型调用，放入线程可避免阻塞当前的异步文章生成流程。不可访问
    的图片不会中断文字生成；二维码则必须同时从内容 Agent 的槽位输入和最终 DOM
    中移除，避免模型为二维码生成替代图。
    """
    slots_with_urls = [
        slot for slot in blueprint.image_slots
        if slot.source_url.startswith(("http://", "https://"))
    ]
    if not slots_with_urls:
        return {}, set()

    from app.services.reference_media_analysis_service import analyze_reference_images

    analysis = await asyncio.to_thread(
        analyze_reference_images,
        [slot.source_url for slot in slots_with_urls],
        understand_images,
    )
    visual_descriptions = {
        slots_with_urls[item.source_index].slot_id: item.description
        for item in analysis.usable_images
    }
    excluded_image_slot_ids = {
        slots_with_urls[source_index].slot_id
        for source_index in analysis.skipped_qrcode_source_indexes
    }
    return visual_descriptions, excluded_image_slot_ids


def _build_html_imitation_prompt(
    state: ArticleState,
    blueprint_payload: dict,
    visual_descriptions: dict,
) -> str:
    """构建内容文字分析与图片生成意图的统一 JSON 任务。

    不将投喂文章全文放入 Prompt，既减少模型复述风险，也确保模型只能参照结构、
    目标长度与图片视觉特征围绕用户主题重新创作。
    """
    outline_text = _build_outline_text(state)
    knowledge_context = state.kb_context or "（未提供）"
    # ERP 投喂仿写时，正文知识库与图片背景知识库已经按职责分流。背景规则
    # 只在本次槽位内容 Agent 中完整传入，要求模型将它们落实到每个槽位的
    # ``prompt``；后续每张图生图不再重复携带同一份长规则，从源头减少输入 token。
    image_background_context = state.image_prompt_context or "（未提供）"
    return f"""请围绕用户主题重新创作一篇公众号文章，并严格填充以下 HTML 槽位。

用户主题：{state.topic}
文章标题：{state.title.main_title if state.title else state.topic}
文章大纲：
{outline_text}

目标产品：{state.product_name or state.topic}
品牌与背景知识库：
{knowledge_context}

图片背景知识库（仅用于 image_slots.prompt，必须将全部硬性视觉约束落实到每个图片槽位）：
{image_background_context}

格式 Agent 输出的固定槽位：
{json.dumps(blueprint_payload, ensure_ascii=False)}

图片理解 Agent 输出的参考视觉特征：
{json.dumps(visual_descriptions, ensure_ascii=False)}

规则：
1. text_slots 中每个 id 都必须返回一次，content 为围绕新主题创作的纯文本；长度接近 target_length。
2. 文章标题已由公众号单独展示，任何 text_slots 都不得重复主标题或副标题；第一个 p 槽位必须写开场正文，不能把标题当作导语。
3. image_slots 中每个 id 都必须返回一次。keywords 必须描述目标产品；prompt 必须是可直接交给图生图模型的完整中文视觉提示词，包含图片背景知识库的硬性视觉约束和参考视觉特征，但不能复刻文字、水印或二维码。
4. 不得复制参考文章句子，不得输出 HTML、Markdown 或代码块。
5. 只能返回下列 JSON，不能增加或删除字段：
{{
  "text_slots": [{{"id": "text-1", "content": ""}}],
  "image_slots": [{{"id": "image-1", "keywords": "", "prompt": ""}}]
}}"""


def _index_agent_slots(items: object, primary_field: str) -> dict:
    """将模型返回的槽位数组规整为 ``{slot_id: 内容}`` 映射。

    图片槽位需要保留 keywords 与 prompt 两个字段；文字槽位仅返回 content。异常
    项被忽略，最终由渲染服务输出空槽位而不是错误替换到其他节点。
    """
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        slot_id = str(item["id"])
        if primary_field == "content":
            result[slot_id] = str(item.get("content", ""))
        else:
            result[slot_id] = {
                "keywords": str(item.get("keywords", "")),
                "prompt": str(item.get("prompt", "")),
            }
    return result


async def _repair_duplicate_article_title_slots(
    state: ArticleState,
    blueprint,
    text_by_slot: dict,
) -> dict:
    """只重写误返回文章标题的 HTML 文字槽位。

    公众号页面会在正文外独立展示标题，模型若把标题再次写入 ``p`` 或标题槽，会让
    用户看到连续两遍标题。直接删除该节点虽然能消除重复，却会损失投喂源原本的
    开场正文，因此这里只对异常槽位追加一次小范围修复调用，其余已生成段落保持
    不变。修复结果仍重复标题或为空时明确失败，阻止有缺陷的文章继续发布。
    """

    candidate_titles = [
        title.strip()
        for title in (
            state.title.main_title if state.title else "",
            state.title.sub_title if state.title else "",
        )
        if title and title.strip()
    ]
    if not candidate_titles:
        return text_by_slot

    duplicate_slots = [
        slot
        for slot in blueprint.text_slots
        if any(
            _is_same_title(str(text_by_slot.get(slot.slot_id, "")), title)
            for title in candidate_titles
        )
    ]
    if not duplicate_slots:
        return text_by_slot

    repair_payload = [
        {
            "id": slot.slot_id,
            "tag": slot.tag_name,
            "target_length": slot.target_length,
        }
        for slot in duplicate_slots
    ]
    repair_prompt = f"""修复微信公众号正文中误写成文章标题的文字槽位。

文章标题：{state.title.main_title if state.title else state.topic}
目标产品：{state.product_name or state.topic}
文章大纲：
{_build_outline_text(state)}

需要修复的槽位：
{json.dumps(repair_payload, ensure_ascii=False)}

要求：
1. 每个槽位都写成与上下文衔接的正文或章节观点，长度接近 target_length。
2. 第一个 p 槽位应从真实需求或使用场景切入，不能重复文章标题，不能只写产品名。
3. 不得虚构知识库未提供的参数、价格、品牌、案例或效果。
4. 只能返回 JSON：{{"text_slots":[{{"id":"text-1","content":""}}]}}。
"""
    raw = await _call_llm(
        "你是微信公众号正文槽位修复 Agent。只补写指定正文槽位，不能重复文章标题。",
        repair_prompt,
        temperature=0.6,
    )
    repaired_data = _parse_json_response(raw)
    repaired_slots = _index_agent_slots(repaired_data.get("text_slots", []), "content")
    merged_slots = dict(text_by_slot)
    for slot in duplicate_slots:
        repaired_content = str(repaired_slots.get(slot.slot_id, "")).strip()
        if not repaired_content or any(
            _is_same_title(repaired_content, title) for title in candidate_titles
        ):
            raise ValueError(f"正文槽位 {slot.slot_id} 重复文章标题且自动修复失败")
        merged_slots[slot.slot_id] = repaired_content
    return merged_slots


def _compose_html_image_slot_prompts(
    image_by_slot: dict,
    visual_descriptions: dict,
    excluded_image_slot_ids: set[str],
) -> dict:
    """为 HTML 图片槽位强制合成最终生图提示词。

    内容 Agent 只提供新主体和补充描述，参考图片的视觉分析由程序写入最终提示词。
    二维码槽位即使被模型异常返回也会被忽略，确保不会重新进入后续图片生成流程。
    """
    from app.services.reference_image_imitation_service import compose_visual_imitation_prompt

    result = {}
    for slot_id, image_data in image_by_slot.items():
        if slot_id in excluded_image_slot_ids:
            continue
        subject = str(image_data.get("keywords", "")).strip()
        supplement = str(image_data.get("prompt", "")).strip()
        result[slot_id] = {
            "keywords": subject,
            "prompt": compose_visual_imitation_prompt(
                visual_descriptions.get(slot_id, {}),
                subject=subject,
                supplement=supplement,
            ),
        }
    return result


async def _generate_structured_content(
    state: ArticleState,
    stream_handler: Optional[Callable[[str], None]] = None,
) -> ArticleState:
    """Layout template mode: LLM fills in content for each pre-defined block.

    The template structure is fixed — the LLM only fills ``content`` and
    ``requirement`` fields. Block types, counts, and order are locked.
    """
    from app.constants.prompt import AGENT3_STRUCTURED_PROMPT

    outline_text = _build_outline_text(state)

    # Build the JSON template for the LLM to fill
    t = state.layout_template
    sections_json = []
    for sec_idx, sec in enumerate(t.sections):
        sec_data = {
            "section_role": sec.section_role,
            "blocks": [],
        }
        for b in sec.blocks:
            block_data = {
                "type": b.type,
            }
            if b.type in ("heading", "paragraph", "quote"):
                block_data["role"] = b.role or ""
                if b.length_chars_target:
                    block_data["length_chars_target"] = b.length_chars_target
                block_data["content"] = ""  # LLM fills this
            elif b.type == "image":
                block_data["count"] = b.count
                block_data["requirement"] = ""  # LLM fills: what the image should show
                block_data["alt"] = ""
            elif b.type == "divider":
                block_data["content"] = ""
            else:
                block_data["content"] = ""
            sec_data["blocks"].append(block_data)
        sections_json.append(sec_data)

    prompt = AGENT3_STRUCTURED_PROMPT.format(
        topic=state.topic,
        main_title=state.title.main_title,
        sub_title=state.title.sub_title,
        outline=outline_text,
        sections_json=json.dumps(sections_json, ensure_ascii=False, indent=2),
    )

    # Inject reference articles for context
    if state.style_profile:
        prompt += _build_style_profile_section(state.style_profile)
    if state.reference_articles:
        prompt += _build_reference_articles_section(state.reference_articles)
    if state.kb_context:
        prompt += (
            "\n\n## 品牌与背景知识库（正文和图片要求必须遵守）\n"
            f"{state.kb_context}\n"
        )

    system_msg = (
        "你是一个专业的内容填充专家。你的任务是将模板中的每个 block 填写完整。\n"
        "不要修改模板结构，不要新增或删除 block。\n"
        "输出严格的 JSON 格式，不要包含其他文字。\n"
        "全文必须使用纯中文。"
    )

    def _noop(text: str) -> None:
        pass

    handler = stream_handler or _noop
    raw = await _call_llm_with_streaming(system_msg, prompt, handler, temperature=0.7)

    try:
        data = _parse_json_response(raw)
        filled_sections = data.get("sections", data if isinstance(data, list) else [data])

        # Validate: check section count matches template
        if len(filled_sections) != len(t.sections):
            print(f"  ⚠️ Section count mismatch: LLM returned {len(filled_sections)}, expected {len(t.sections)}")
            # Pad or truncate
            while len(filled_sections) < len(t.sections):
                filled_sections.append({"section_role": "unknown", "blocks": []})
            filled_sections = filled_sections[:len(t.sections)]

        # Validate each section's block count
        for sec_idx, sec in enumerate(filled_sections):
            expected_blocks = len(t.sections[sec_idx].blocks)
            actual_blocks = len(sec.get("blocks", []))
            if actual_blocks != expected_blocks:
                print(f"  ⚠️ Block count mismatch in section {sec_idx}: got {actual_blocks}, expected {expected_blocks}")
                # Rebuild blocks from template, preserving any filled content
                filled = sec.get("blocks", [])
                corrected = []
                for bi, tb in enumerate(t.sections[sec_idx].blocks):
                    if bi < len(filled):
                        corrected.append(filled[bi])
                    else:
                        corrected.append({"type": tb.type, "content": ""})
                sec["blocks"] = corrected

        # Store structured content
        state.content_blocks = filled_sections

        # Also render a flat markdown version with [IMAGE:] placeholders
        # for backward compatibility with the image pipeline
        state.content = _render_structured_blocks(filled_sections)

        print(f"  ✅ 结构化正文完成: {len(filled_sections)} 章节, "
              f"{sum(len(s.get('blocks', [])) for s in filled_sections)} 个内容块")

    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        state.error = f"Failed to parse structured content: {exc}"
        # Fallback: treat raw as content
        state.content = raw

    return state


def _render_structured_blocks(sections: list) -> str:
    """Convert structured content blocks to Markdown with [IMAGE:] placeholders.

    This is the backward-compatible renderer — the image pipeline still
    depends on [IMAGE:] markers to know where to place images.
    """
    lines = []
    image_pos = [0]  # mutable counter for image positions

    for sec in sections:
        for b in sec.get("blocks", []):
            t = b["type"]
            if t == "heading":
                level = b.get("level", 2)
                content = b.get("content", "").strip()
                if content:
                    lines.append(f"{'#' * level} {content}")
            elif t == "paragraph":
                content = b.get("content", "").strip()
                if content:
                    lines.append(content)
                    lines.append("")
            elif t == "image":
                image_pos[0] += 1
                req = b.get("requirement", b.get("content", "image"))
                alt = b.get("alt", req)
                count = b.get("count", 1)
                for _ in range(count):
                    lines.append(f"[IMAGE:position={image_pos[0]},keywords={alt},type=inline]")
                    lines.append("")
            elif t == "quote":
                content = b.get("content", "").strip()
                if content:
                    lines.append(f">{content}")
                    lines.append("")
            elif t == "divider":
                lines.append("---")
                lines.append("")
            elif t == "list":
                items = b.get("items", [])
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

    return "\n".join(lines).strip()


async def agent4_analyze_image_requirements(state: ArticleState) -> ArticleState:
    print(f"  ▶ agent4: 分析配图需求...")
    """Analyse the generated content and determine where images are needed.

    Returns the state with ``image_requirements`` populated.
    """
    if not state.content:
        state.error = "Content is required before image requirement analysis"
        return state

    # HTML 仿写 Agent 已基于原图片槽位生成需求。再次让通用 Agent 根据全文猜测
    # 图片位置会破坏原位关系，因此直接沿用已生成的结构化需求。
    if 'data-ai-image-slot=' in state.content and state.image_requirements:
        return state

    enabled_methods = state.enabled_image_methods or ["DASHSCOPE"]
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
    print(f"  ▶ agent5: 获取配图（共 {len(state.image_requirements)} 张需求）...")
    """Execute image searches for each requirement in parallel.

    This agent dispatches calls to the appropriate image service for each
    requirement and collects the results.

    If ``state.selected_image_urls`` is provided (user pre-selected local images),
    those are used directly instead of searching external sources.
    """
    if not state.image_requirements:
        state.error = "No image requirements to process"
        return state

    # 所有生图入口都需要统一成本兜底，但 HTML 定时任务可以显式提高上限。
    # ArticleState 默认仍为五张，因此旧的 Markdown、结构化文章和历史 LangGraph
    # 节点不会改变行为；只有明确传入的任务配置才会让 Agent 继续处理更多槽位。
    configured_limit = max(
        1,
        min(getattr(state, "max_generated_images", 5) or 5, 30),
    )
    if len(state.image_requirements) > configured_limit:
        logger.info(
            "图片需求由 %d 张限制为 %d 张，额外槽位保持空白",
            len(state.image_requirements),
            configured_limit,
        )
        state.image_requirements = list(state.image_requirements[:configured_limit])

    # ---- ERP 产品参考图生图 ----
    # 定时任务每天只选择一个产品。每个图片槽位都以同一产品原图为参考生成不同场景，
    # 不能走 selected_image_urls 的直插路径，否则不会产生新的背景海报。
    if state.reference_image_url:
        from app.services.image_generation_models import ImageGenerationRequest
        from app.services.image_generation_service import image_generation_service

        results: List[ImageResult] = []
        for req in state.image_requirements:
            slot_prompt = (req.prompt or "").strip()
            if state.reference_html and not slot_prompt:
                # HTML ERP 路径的背景规则只由槽位内容 Agent 编译一次。此处若允许
                # 用产品关键词兜底，虽然能够少传 token，却会丢失品牌场景、色彩等
                # 硬约束并让图像质量退化，因此必须中止本次发布。
                raise RuntimeError(
                    f"图生图失败：图片槽位 {req.placeholder_id or req.position} "
                    "缺少完整视觉提示词"
                )
            base_prompt = (slot_prompt or req.keywords or req.section_title).strip()
            if not base_prompt:
                raise RuntimeError(
                    f"图生图失败：图片槽位 {req.placeholder_id or req.position} 缺少视觉提示词"
                )
            product_rule = (
                f"目标产品：{state.product_name}。必须保留参考图中该产品的主体结构、"
                "材质和关键设计特征，只根据下述规则替换背景。\n\n"
                if state.product_name else ""
            )
            if state.reference_html:
                # HTML 槽位 Agent 已一次性读取完整图片背景知识库，并把规则写入
                # 当前槽位的 ``prompt``。此处不得逐张重复拼接长知识库，否则五张
                # 图片会产生五倍相同输入 token；空提示词已在上方明确阻止发布。
                prompt = product_rule + base_prompt
            else:
                # 非 HTML 的旧文章路径没有统一的槽位内容 Agent，背景规则尚未被
                # 编译进单图提示词。为兼容旧任务并保证视觉质量，仍保留原有注入。
                brand_context = (state.image_prompt_context or "").strip()[:4000]
                prompt = product_rule + (
                    f"品牌视觉约束：{brand_context}\n\n{base_prompt}"
                    if brand_context else base_prompt
                )
            generated = await image_generation_service.generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    tenant_id=state.tenant_id,
                    reference_image_bytes=state.reference_image_bytes,
                    reference_content_type=state.reference_content_type,
                    reference_image_url=state.reference_image_url,
                    size="1024*1365",
                    no_text=True,
                )
            )
            url = generated.url
            if not url:
                # 图生图是定时任务的硬依赖，少一张会造成正文槽位和素材库记录错位；
                # 不允许把空 URL 静默带到发布阶段伪装成成功文章。
                raise RuntimeError(f"图生图失败：第 {req.position} 张图片未返回有效地址")
            results.append(ImageResult(
                position=req.position,
                url=url,
                method=(
                    f"{generated.provider}-fallback"
                    if generated.fallback_used
                    else generated.provider
                ),
                keywords=req.keywords or "",
                section_title=req.section_title,
                description=prompt,
                placeholder_id=req.placeholder_id,
            ))
        state.images = results
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

    total_requirements = len(state.image_requirements)
    for image_index, req in enumerate(state.image_requirements, start=1):
        method = req.image_source or "DASHSCOPE"
        keywords = req.keywords or req.section_title or ""
        final_prompt = req.prompt or keywords

        # 控制台日志是排查“视觉分析是否真正传入生图模型”的关键证据。
        # 仅记录提示词和结果摘要，不记录任何服务密钥或 HTTP 鉴权信息。
        print(f"\n  [图片生成 {image_index}/{total_requirements}]")
        print(f"  ├─ 槽位: {req.placeholder_id or req.position}")
        print(f"  ├─ 主体: {keywords[:160]}")
        print(f"  ├─ 最终提示词 ({len(final_prompt)}字): {final_prompt[:1200]}")

        url = await strategy.execute(
            method,
            keywords,
            prompt=final_prompt,
            tenant_id=state.tenant_id,
        )
        print(f"  └─ 生成结果: {(url or '失败，未返回图片地址')[:240]}")

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

    # HTML 仿写模式由结构服务在原 ``img`` 节点上标记图片槽位。此处必须优先
    # 原位替换，不能沿用 Markdown 图片标记的“插入一段新图片”策略，否则图片会
    # 被追加到文末或离开原文版式位置。
    if 'data-ai-image-slot=' in content:
        from app.services.html_imitation_service import replace_html_image_slots

        image_urls_by_slot = {
            image.placeholder_id: image.url
            for image in state.images
            if image.placeholder_id and image.url
        }
        content = replace_html_image_slots(content, image_urls_by_slot)

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

            is_gallery = 'type=gallery' in raw
            url = images_by_position.get(pos, "")
            if url:
                if is_gallery:
                    return f'<img class="gallery-img" data-pos="{pos}" src="{url}" alt="{alt}" />'
                return (
                    f'{alt}\n\n'
                    f'<img src="{url}" alt="{alt}" '
                    f'style="width:100%;max-width:640px;border-radius:8px;display:block;margin:16px auto;" />'
                )
            return ""

        content = re.sub(r"\[IMAGE:(.*?)\]", _replace_placeholder, content)

    # Post-processing: remove any remaining [IMAGE:] placeholders
    remaining = re.findall(r'\[IMAGE:[^\]]*\]', content)
    if remaining:
        logger.warning("Found %d unreplaced [IMAGE:] placeholders — removing them", len(remaining))
    content = re.sub(r'\[IMAGE:[^\]]*\]', '', content)

    # Post-processing: normalize excessive blank lines (4+ → 2)
    content = re.sub(r'\n{4,}', '\n\n', content)

    # Post-processing: normalize 3+ spaces at line starts
    content = re.sub(r'^ {3,}', '', content, flags=re.MULTILINE)

    # Post-processing: group consecutive gallery images into carousel
    content = _wrap_gallery_images(content)

    # 去掉 AI 生成内容开头的标题（已由前端独立展示）
    content = _strip_leading_title(content, state)

    # HTML 仿写渲染器会在正文末尾统一追加固定页脚。这里识别任意页脚标记，避免
    # 图片合并阶段把同一份电话和二维码再追加一次。
    footer_already_applied = "data-ai-footer-template=" in content
    if state.footer_template and not footer_already_applied:
        footer = state.footer_template.strip()
        if footer:
            if content.lstrip().startswith("<"):
                from app.services.footer_template_service import render_footer_template_html

                footer_html = render_footer_template_html(footer)
                if footer_html:
                    content = f"{content}\n<div data-ai-footer-template=\"appended\">{footer_html}</div>"
            else:
                content = f"{content}\n\n{footer}"

    # 无论内容来自 HTML 仿写、结构化模板还是普通 Markdown，AI 图片说明都由
    # 程序统一追加。该函数具备幂等性，因此多个生成入口复用此步骤不会重复出现。
    from app.services.article_publication_polish_service import append_ai_image_disclaimer

    state.full_content = append_ai_image_disclaimer(content)
    return state


def _strip_leading_title(content: str, state: ArticleState) -> str:
    """去掉正文开头可能重复的标题，同时兼容 HTML 与 Markdown。

    HTML 仿写主流程会在槽位阶段主动修复标题，这里仍保留确定性兜底，覆盖旧
    LangGraph 节点或历史任务直接返回整段 HTML 的情况。只检查第一个有文本的
    内容块，避免误删正文中后续正常提及产品标题的段落。
    """
    if content.lstrip().startswith("<"):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")
        first_text_block = soup.find(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "li"]
        )
        if first_text_block is not None and state.title:
            first_text = first_text_block.get_text(" ", strip=True)
            titles = [state.title.main_title, state.title.sub_title]
            if any(title and _is_same_title(first_text, title) for title in titles):
                first_text_block.decompose()
        return str(soup)

    lines = content.split("\n")
    while lines:
        stripped = lines[0].strip()
        # 去掉 # 标题 格式
        if stripped.startswith("# ") or stripped.startswith("## "):
            title_text = stripped.lstrip("#").strip()
            # 如果是标题本身，去掉
            if state.title and (title_text == state.title.main_title or title_text == state.title.sub_title):
                lines.pop(0)
                continue
            # 如果内容里只有一个 # 标题，也去掉（AI 经常生成）
            if not state.title:
                lines.pop(0)
                continue
        # 去掉空的头行
        elif not stripped:
            lines.pop(0)
            continue
        break
    return "\n".join(lines)


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


def _build_layout_section(state: ArticleState) -> str:
    """Build a layout template section for prompt injection.

    When ``state.layout_template`` is present, this generates constraints
    for Agent 2 (outline) and Agent 3 (content) to follow the reference
    article's structure exactly.
    """
    if not state.layout_template:
        return ""

    t = state.layout_template
    lines = [
        "\n\n## 版式结构约束（必须严格遵循）",
        f"这篇文章的结构来自参考文章的版式分析。",
        f"全文共 {len(t.sections)} 个章节，{t.total_paragraph_count} 个段落，{t.total_image_count} 张配图。",
        f"结尾风格：{t.ending_style}",
        "",
        "### 章节顺序与内容块模板（严格按照以下顺序和块类型生成，不得增减章节或调整顺序）：",
    ]

    for i, sec in enumerate(t.sections):
        lines.append(f"\n---\n章节 {i+1}：{sec.section_role}")
        for j, b in enumerate(sec.blocks):
            blocks_desc = f"  [{j+1}] 类型：{b.type}"
            if b.role:
                blocks_desc += f"，功能：{b.role}"
            if b.level:
                blocks_desc += f"，标题层级：h{b.level}"
            if b.style_pattern:
                blocks_desc += f"，标题句式：\"{b.style_pattern}\""
            if b.length_chars_target and b.length_chars_target > 10:
                blocks_desc += f"，目标字数：约{b.length_chars_target}字"
            if b.type == "image" and b.count > 0:
                blocks_desc += f"，数量：{b.count}张"

            lines.append(blocks_desc)

    lines.append(f"\n### 结尾要求")
    lines.append(f"结尾风格必须为「{t.ending_style}」，不得使用其他风格。")

    if t.layout_features:
        lines.append(f"\n### 整体版式特征")
        for feat in t.layout_features:
            lines.append(f"- {feat}")

    lines.append("")
    lines.append("【重要】章节标题必须围绕用户主题重新创作，不得照搬参考文章的标题措辞。")
    lines.append("【重要】图片位置已由模板确定，在正文中生成 [IMAGE:] 占位符时必须匹配模板指定的图片数量和位置。")

    return "\n".join(lines)


def _build_reference_articles_section(articles: list) -> str:
    """Build a reference articles section for prompt injection.

    Detects article format type (pure-image gallery vs text) and
    injects appropriate format instructions for the AI to replicate.
    """
    if not articles:
        return ""

    lines = ["\n\n## 参考文章格式与风格说明"]
    has_image_format = False
    has_text_format = False

    for i, article_text in enumerate(articles, start=1):
        lines.append(f"\n### 参考文章 {i}")

        # Detect pure-image gallery format (all lines are ![](url) markdown images)
        stripped = article_text.strip()
        blocks = [b for b in stripped.split('\n\n') if b.strip()]
        image_blocks = [b for b in blocks if b.startswith('![](http')]
        is_image_gallery = len(blocks) > 0 and len(image_blocks) == len(blocks)

        if is_image_gallery:
            has_image_format = True
            image_count = len(blocks)
            lines.append(f"""格式类型：纯图片画廊
图片数量：{image_count} 张
展示方式：主图展示区 + 底部缩略图横向滑动列表
交互方式：点击缩略图可切换主图，当前选中缩略图带高亮描边
缩略图导航：超过可视范围可左右滑动或点击箭头查看更多""")
        else:
            has_text_format = True
            # 保留 [IMAGE:] 标记以便 AI 看到图片排版
            cleaned = re.sub(
                r'^.*?(?:45度|俯拍|仰拍|微距|特写|暖光|逆光|打光|布光).*?(?:场景|效果|展示|组合|特写).*?\n',
                '', article_text, flags=re.MULTILINE,
            )
            cleaned = cleaned.strip()
            excerpt = cleaned[:3000]
            if len(cleaned) > 3000:
                excerpt += "\n\n...（格式摘要）"
            if excerpt and len(excerpt) > 50:
                lines.append(f"格式类型：图文混排版\n完整参考格式：\n{excerpt}")

    # Output format rules
    lines.append("""

## ⚠️ 输出格式规则

### 所有文章通用规则：
1. **标题加粗**：所有小标题/段落总结语/独立成行的主题句，用 `**加粗**` 包裹
2. **段落间距**：段与段之间空一行""")

    if has_image_format:
        lines.append("""
### 纯图片画廊格式规则（仅当参考文章为纯图片格式时适用）：
1. **不生成任何正文文字**，只生成图片标记
2. 每张图片使用格式：`[IMAGE:position=N,keywords=图片内容描述,type=gallery]`
3. 生成 6～10 张图片标记，用于展示画廊效果
4. keywords 描述图片应展示的内容（如「产品正面全景」「细节特写」等）""")

    if has_text_format:
        lines.append("""
### 图文混排格式规则（仅当参考文章为图文格式时适用）：
1. **完整段落**：每一段必须是多句话连贯而成的完整段落
2. **图片标记**：每篇文章必须插入4～8张配图标记 `[IMAGE:position=N,keywords=图片内容描述,type=T]`
   - keywords 只写图片内容（如「客厅全景」「教师办公」「产品细节」），不得包含任何摄影术语""")

    lines.append("""
### 【绝对禁止】以下内容不得出现在正文中：
- 拍摄角度、光线、构图等任何图片描述文字
- 具体产品名、品牌名、价格、联系方式
- 参考文章中的专属名词和特定产品描述
- 摄影术语：俯拍、仰拍、特写、微距、暖光、逆光、45度、打光、布光、景深、背景虚化

### 重要：
- 你的文章内容必须围绕用户给定的**主题**来写
- 严格遵守参考文章的**格式类型**（纯图片画廊 或 图文混排），不要混合使用""")

    return "\n".join(lines)


def _wrap_gallery_images(content: str) -> str:
    """Group consecutive ``<img class="gallery-img">`` into a carousel.
    All styles and interactions are inline — no external CSS needed."""
    import re as _re
    pattern = r'(<img class="gallery-img"[^>]*/>\s*)+'
    def _wrap(m: _re.Match) -> str:
        imgs = m.group(0)
        items = _re.findall(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/>', imgs)
        if not items:
            return imgs
        thumbs = ""
        for i, (url, alt) in enumerate(items):
            border = '#07c160' if i == 0 else 'transparent'
            op = '1' if i == 0 else '0.6'
            thumbs += (
                f'<div style="flex:0 0 80px;height:60px;border-radius:6px;overflow:hidden;'
                f'cursor:pointer;border:2px solid {border};opacity:{op};transition:all .2s;" '
                f'onclick="let p=this.parentElement;'
                f'p.querySelectorAll(\'>div\').forEach(d=>{{d.style.border=\'2px solid transparent\';d.style.opacity=\'0.6\'}});'
                f'this.style.border=\'2px solid #07c160\';this.style.opacity=\'1\';'
                f'p.parentElement.querySelector(\'.gallery-main img\').src=\'{url}\';">'
                f'<img src="{url}" alt="{alt}" loading="lazy" '
                f'style="width:100%;height:100%;object-fit:cover;display:block;" />'
                f'</div>'
            )
        fu, fa = items[0]
        return (
            f'<div class="image-gallery" style="margin:16px 0;">'
            f'<div class="gallery-main" style="width:100%;background:#f0f0f0;border-radius:8px;'
            f'overflow:hidden;display:flex;align-items:center;justify-content:center;min-height:300px;">'
            f'<img src="{fu}" alt="{fa}" '
            f'style="max-width:100%;max-height:65vh;width:auto;height:auto;object-fit:contain;" />'
            f'</div>'
            f'<div style="display:flex;gap:8px;margin-top:12px;overflow-x:auto;padding:4px 0;">'
            f'{thumbs}</div></div>'
        )
    return _re.sub(pattern, _wrap, content, flags=_re.DOTALL)


# ---------------------------------------------------------------------------
# Structured content: extract image slots & render final output
# ---------------------------------------------------------------------------


def extract_image_slots_from_blocks(sections: list) -> tuple:
    """Extract ImageRequirement-like slots from structured content blocks.

    Skips image blocks whose requirement/alt text suggests a QR code
    or purely decorative element (no meaningful content to generate).

    Returns (image_slots, updated_sections).
    """
    from app.schemas.article import ImageRequirement

    # Keywords that indicate an image is a QR code / contact card / pure decoration
    _QR_KEYWORDS = ["二维码", "qrcode", "qr code", "微信", "公众号", "水印",
                     "电话", "手机", "联系", "扫码", "关注", "小程序"]

    slots = []
    pos_counter = [0]

    for sec in sections:
        for b in sec.get("blocks", []):
            if b["type"] == "image":
                count = b.get("count", 1)
                req = b.get("requirement", b.get("content", "image"))
                alt = b.get("alt", req)
                combined = (req + " " + alt).lower()

                # Skip QR codes and contact info
                if any(kw in combined for kw in _QR_KEYWORDS):
                    # Mark as skipped so the renderer won't insert a placeholder
                    b["skipped"] = True
                    print(f"  🚫 跳过二维码图片: {req[:60]}")
                    continue

                for _ in range(count):
                    pos_counter[0] += 1
                    slot_id = f"slot_{pos_counter[0]}"
                    slots.append({
                        "slot_id": slot_id,
                        "position": pos_counter[0],
                        "requirement": req,
                        "alt": alt,
                    })
                    b["slot_id"] = slot_id  # tag for later rendering

    return slots, sections


def _inline_md_to_html(text: str) -> str:
    """Convert inline markdown formatting to HTML tags.

    - ``**bold**`` / ``__bold__`` → ``<strong>bold</strong>``
    - ``*italic*`` / ``_italic_`` → ``<em>italic</em>``
    """
    import re as _re
    text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = _re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    text = _re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<em>\1</em>', text)
    return text


def render_final_content(
    sections: list,
    image_urls: dict,
    footer_template: str = "",
) -> str:
    """Render structured content blocks to final HTML with images placed.

    Args:
        sections: Content blocks with LLM-filled content.
        image_urls: Dict mapping slot_id → image_url.
        footer_template: Optional footer text.

    Returns:
        Final HTML string ready for storage/display.
    """
    parts = []

    for sec in sections:
        for b in sec.get("blocks", []):
            t = b["type"]

            # Skip blocks explicitly marked (e.g. QR codes)
            if b.get("skipped"):
                continue

            if t == "heading":
                level = b.get("level", 2)
                content = _inline_md_to_html(b.get("content", "").strip())
                if content:
                    size_map = {1: "22px", 2: "18px", 3: "16px"}
                    font_size = size_map.get(level, "16px")
                    parts.append(
                        f'<h{level} style="font-size:{font_size};font-weight:600;'
                        f'margin:28px 0 16px;line-height:1.5;">{content}</h{level}>'
                    )

            elif t == "paragraph":
                content = _inline_md_to_html(b.get("content", "").strip())
                if content:
                    parts.append(
                        f'<p style="font-size:15px;line-height:1.8;color:#333;'
                        f'margin-bottom:18px;">{content}</p>'
                    )

            elif t == "image":
                slot_id = b.get("slot_id", "")
                if not slot_id:
                    continue
                url = image_urls.get(slot_id, "")
                alt = b.get("alt", b.get("requirement", "image"))
                if url:
                    parts.append(
                        f'<img src="{url}" alt="{alt}" '
                        f'style="width:100%;max-width:640px;border-radius:8px;'
                        f'display:block;margin:18px auto;" />'
                    )

            elif t == "quote":
                content = _inline_md_to_html(b.get("content", "").strip())
                if content:
                    parts.append(
                        f'<blockquote style="background:#f7f7f7;border-left:4px solid #07c160;'
                        f'margin:16px 0;padding:12px 16px;color:#555;'
                        f'font-size:14px;line-height:1.7;">{content}</blockquote>'
                    )

            elif t == "divider":
                parts.append('<hr style="border:none;border-top:1px solid #e8e8e8;margin:24px 0;" />')

            elif t == "list":
                items = b.get("items", [])
                if items:
                    list_html = "\n".join(
                        f'<li style="margin-bottom:6px;font-size:15px;line-height:1.7;">{_inline_md_to_html(item)}</li>'
                        for item in items
                    )
                    parts.append(f'<ul style="padding-left:20px;margin:12px 0;">{list_html}</ul>')

    content = "\n".join(parts)

    # Append footer
    if footer_template:
        footer = footer_template.strip()
        if footer:
            from app.services.footer_template_service import render_footer_template_html

            footer_html = render_footer_template_html(footer)
            if footer_html:
                content += f'\n<div data-ai-footer-template="appended">{footer_html}</div>'

    return content
