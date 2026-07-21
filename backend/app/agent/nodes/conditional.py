"""Conditional routing functions for the article generation LangGraph.

These functions inspect the current ``ArticleGenState`` and return the name
of the next node to execute, enabling retry / error-recovery flows.
"""

import logging
from typing import Literal

from app.agent.state import ArticleGenState

logger = logging.getLogger(__name__)


def should_retry(state: ArticleGenState) -> Literal[
    "generate_titles",
    "generate_outline",
    "generate_content",
    "continue",
]:
    """检查是否需要重试某个步骤。

    Inspects ``state["error"]``:

    * If ``error`` starts with ``"title:"``    -> retry ``"generate_titles"``
    * If ``error`` starts with ``"outline:"``   -> retry ``"generate_outline"``
    * If ``error`` starts with ``"content:"``   -> retry ``"generate_content"``
    * Otherwise                                  -> ``"continue"`` (proceed)

    This function is designed to be used as a ``conditional_edges`` callback
    in LangGraph after any node that may set ``state["error"]``.
    """
    error = state.get("error")
    if not error:
        return "continue"

    error_str = str(error).lower().strip()

    if error_str.startswith("title:"):
        logger.warning("Retrying title generation: %s", error)
        return "generate_titles"

    if error_str.startswith("outline:"):
        logger.warning("Retrying outline generation: %s", error)
        return "generate_outline"

    if error_str.startswith("content:"):
        logger.warning("Retrying content generation: %s", error)
        return "generate_content"

    # Unknown error — let the graph continue and surface the error downstream
    logger.warning("Unknown error prefix, continuing: %s", error)
    return "continue"


def has_error(state: ArticleGenState) -> Literal["error_end", "continue"]:
    """简单错误检查路由。

    Returns ``"error_end"`` if ``state["error"]`` is set, otherwise
    ``"continue"``.  Useful as a pre-flight check before entering the
    main pipeline.
    """
    if state.get("error"):
        logger.error("Aborting pipeline due to error: %s", state["error"])
        return "error_end"
    return "continue"
