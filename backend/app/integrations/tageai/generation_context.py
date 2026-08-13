"""TaGeAI 调用参数到内容生成状态的受控转换。

Gateway 传入的 ``generation_config`` 只是持久化载体，不能把它当作已经被生成
流水线消费。这里集中完成参数校验、URL 参考解析和 ``ArticleState`` 注入，保证
仿写任务无法在缺少可用参考时静默退化为普通自由创作。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.schemas.article import ArticleState


class TageAiGenerationContextError(ValueError):
    """TaGeAI 受控生成上下文无法满足时抛出的可诊断错误。

    该异常由队列消费者向上抛出，使 ContentJob 进入失败状态，而不是生成一篇没有
    仿写参考的普通文章后再伪装为成功。
    """


async def apply_tageai_generation_context(
    state: ArticleState,
    generation_config: Mapping[str, Any] | None,
) -> ArticleState:
    """将 TaGeAI 的受控输入写入本次真实生成状态。

    只有带 ``tageai_operation`` 的任务会进入本模块，普通平台任务保持原有行为。标题
    覆盖和内容约束同时适用于生成、仿写；仿写额外要求解析出可供标题、大纲、正文
    Agent 使用的正文参考。
    """

    config = generation_config or {}
    operation = str(config.get("tageai_operation") or "").strip().lower()
    if not operation:
        return state
    if operation not in {"generate", "imitate"}:
        raise TageAiGenerationContextError(f"不支持的 TaGeAI 操作类型：{operation}")

    title_override = str(config.get("title_override") or "").strip()
    if title_override:
        state.title_override = title_override

    state.content_constraints = _normalize_constraints(config.get("content_constraints"))
    if operation != "imitate":
        return state

    reference_text = await _resolve_reference_text(config.get("tageai_reference"))
    # reference_articles 已被标题、大纲和正文 Agent 统一注入提示词，是内容仿写的
    # 最小共享载体。这里不设置 reference_html，避免没有版式合同的 URL 任务误入
    # HTML DOM 回填流程。
    state.reference_articles = [reference_text]
    return state


def _normalize_constraints(raw_constraints: Any) -> list[str]:
    """清理约束输入，避免空值和非字符串内容污染模型提示词。"""

    if not isinstance(raw_constraints, list):
        return []
    return [str(item).strip() for item in raw_constraints if str(item).strip()]


async def _resolve_reference_text(raw_reference: Any) -> str:
    """将文本或 URL 参考解析为可直接投喂给生成 Agent 的正文。

    URL 复用现有抓取服务，沿用其 SSRF 校验和正文抽取逻辑。当前平台没有定义
    ``asset_ref`` 的可解析语义，因此明确失败，不能把未解析的标识符当作文章正文。
    """

    if not isinstance(raw_reference, Mapping):
        raise TageAiGenerationContextError("仿写任务缺少受控参考内容")

    reference_type = str(raw_reference.get("type") or "").strip().lower()
    reference_value = str(raw_reference.get("value") or "").strip()
    if not reference_value:
        raise TageAiGenerationContextError("仿写参考内容不能为空")

    if reference_type == "text":
        return reference_value
    if reference_type == "url":
        from app.services.feed_service import _fetch_single_url

        fetched = await _fetch_single_url(reference_value)
        reference_text = str((fetched or {}).get("body_markdown") or "").strip()
        if not reference_text:
            raise TageAiGenerationContextError("无法抓取仿写参考 URL 的有效正文")
        return reference_text

    raise TageAiGenerationContextError(
        f"不支持的仿写参考类型：{reference_type or 'empty'}；当前仅支持 text 和 url"
    )
