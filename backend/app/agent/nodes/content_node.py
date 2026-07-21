"""LangGraph node for full article content generation (Agent 3)."""

import json
import logging
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.state import ArticleGenState
from app.config import settings
from app.constants.prompt import PromptConstant

logger = logging.getLogger(__name__)


def _build_content_prompt(state: ArticleGenState) -> str:
    """Build the full content-generation prompt from the current state."""
    selected_title = state.get("selected_title") or {}
    main_title = selected_title.get("main_title", "") if selected_title else ""
    sub_title = selected_title.get("sub_title", "") if selected_title else ""

    outline = state.get("outline")
    style = state.get("style") or ""
    image_source = state.get("image_source", "pexels")

    # Serialise the outline into a readable text format
    outline_text = ""
    if outline:
        sections = outline.get("sections", []) if isinstance(outline, dict) else outline.sections
        for sec in sections:
            sec_title = sec.get("title", "") if isinstance(sec, dict) else sec.title
            sec_points = sec.get("points", []) if isinstance(sec, dict) else sec.points
            outline_text += f"\n## {sec_title}\n"
            for pt in sec_points:
                outline_text += f"- {pt}\n"

    style_section = PromptConstant.get_style_prompt(style) if style else ""

    prompt = PromptConstant.AGENT3_CONTENT_PROMPT.format(
        main_title=main_title,
        sub_title=sub_title,
        outline_text=outline_text or "（无大纲）",
        style_section=style_section,
    )

    # Append image source instruction
    if image_source == "local":
        prompt += "\n\n【图片来源说明】优先使用本地素材库中的图片（类型标记为 LOCAL）。"
    elif image_source == "pexels":
        prompt += "\n\n【图片来源说明】优先使用 Pexels 图库图片（类型标记为 PEXELS）。"

    return prompt


def generate_content_node(state: ArticleGenState) -> dict:
    """生成文章正文。

    Uses ChatOpenAI (streaming-capable) to generate the full article body in
    Markdown.  The model is instructed to insert ``[IMAGE:...]`` placeholders
    at appropriate positions for later image analysis & filling.
    """
    prompt = _build_content_prompt(state)

    logger.info(
        "Generating content for title=%s",
        state.get("selected_title", {}).get("main_title", "") if state.get("selected_title") else "",
    )

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        temperature=0.8,
        max_tokens=4096,
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
        content = "".join(text_parts)

    logger.info("Generated content (%d characters)", len(content or ""))
    return {"content": content}


async def generate_content_stream(state: ArticleGenState) -> AsyncGenerator[str, None]:
    """Async streaming variant — yields content chunks for real-time UI.

    This is the preferred entry point when the caller supports streaming
    (e.g. Server-Sent Events or WebSocket).
    """
    prompt = _build_content_prompt(state)

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        temperature=0.8,
        max_tokens=4096,
        streaming=True,
    )

    full_content: list[str] = []
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        text = ""
        c = chunk.content
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("text"):
                    text += block["text"]
        if text:
            full_content.append(text)
            yield text
