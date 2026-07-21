"""LangGraph node for title generation (Agent 1)."""

import json
import logging

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.state import ArticleGenState
from app.config import settings
from app.constants.prompt import PromptConstant
from app.schemas.article import TitleOption

logger = logging.getLogger(__name__)

# Default LLM instance shared across nodes that do not need streaming
_llm: ChatOpenAI | None = None


def _get_llm(**kwargs) -> ChatOpenAI:
    """Return a shared or fresh ChatOpenAI instance pointing at DashScope."""
    global _llm
    if not kwargs and _llm is not None:
        return _llm
    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        **kwargs,
    )
    if not kwargs:
        _llm = llm
    return llm


def generate_titles_node(state: ArticleGenState) -> dict:
    """生成标题方案。

    Calls ChatOpenAI (DashScope) with the title-generation prompt, parses the
    JSON response into ``TitleOption`` instances, and stores them in state.
    """
    topic = state.get("topic", "")
    style = state.get("style")

    prompt = PromptConstant.AGENT1_TITLE_PROMPT.replace("{topic}", topic)

    # Append style-specific requirements if a style is selected
    if style:
        style_section = PromptConstant.get_style_prompt(style)
        if style_section:
            prompt += "\n" + style_section

    logger.info("Generating title options for topic=%s style=%s", topic, style)

    llm = _get_llm()
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    # Parse JSON response — the model might return a list or a dict with a key
    title_options_data = json.loads(content)
    if isinstance(title_options_data, dict):
        # Some prompts return {"title_options": [...]}
        title_options_data = title_options_data.get("title_options", title_options_data)
    if not isinstance(title_options_data, list):
        title_options_data = [title_options_data]

    title_options = [TitleOption(**item) for item in title_options_data]

    logger.info("Generated %d title options", len(title_options))
    return {"title_options": [t.model_dump() for t in title_options]}
