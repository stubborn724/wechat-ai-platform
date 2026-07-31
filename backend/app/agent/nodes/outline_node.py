"""LangGraph node for outline generation (Agent 2)."""

import json
import logging
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.state import ArticleGenState
from app.config import settings
from app.constants.prompt import PromptConstant
from app.schemas.article import OutlineResult, OutlineSection

logger = logging.getLogger(__name__)


def _build_outline_prompt(state: ArticleGenState) -> str:
    """Build the full outline-generation prompt from the current state."""
    topic = state.get("topic", "")
    selected_title = state.get("selected_title") or {}
    main_title = selected_title.get("main_title", "") if selected_title else ""
    sub_title = selected_title.get("sub_title", "") if selected_title else ""
    user_description = state.get("user_description") or ""
    style = state.get("style") or ""

    style_section = PromptConstant.get_style_prompt(style) if style else ""

    prompt = PromptConstant.AGENT2_OUTLINE_PROMPT.format(
        topic=topic,
        main_title=main_title,
        sub_title=sub_title,
        style=style or "default",
        user_description=user_description or "无",
        style_section=style_section,
        section_count="4-6",
    )

    # Append user description section if provided
    if user_description:
        prompt += "\n" + PromptConstant.AGENT2_DESCRIPTION_SECTION.format(
            user_description=user_description,
        )

    return prompt


def generate_outline_node(state: ArticleGenState) -> dict:
    """生成文章大纲。

    Uses ChatOpenAI with streaming to generate a structured outline from the
    selected title.  Parses the JSON response into ``OutlineResult``.
    """
    prompt = _build_outline_prompt(state)

    logger.info(
        "Generating outline for topic=%s title=%s",
        state.get("topic"),
        state.get("selected_title", {}).get("main_title", "") if state.get("selected_title") else "",
    )

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        temperature=0.7,
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    # Parse JSON — handle both bare array and {"sections": [...]} wrappers
    outline_data = json.loads(content)
    if isinstance(outline_data, dict):
        outline_data = outline_data.get("sections", outline_data)
    if not isinstance(outline_data, list):
        outline_data = [outline_data]

    sections = [OutlineSection(**item) for item in outline_data]
    outline = OutlineResult(sections=sections)

    logger.info("Generated outline with %d sections", len(sections))
    return {"outline": outline.model_dump()}


async def generate_outline_stream(state: ArticleGenState) -> AsyncGenerator[str, None]:
    """Async generator version — yields content chunks for streaming UI.

    Useful when the frontend wants to display outline generation progress.
    """
    prompt = _build_outline_prompt(state)

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        temperature=0.7,
        streaming=True,
    )

    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        content = chunk.content
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    yield block["text"]
