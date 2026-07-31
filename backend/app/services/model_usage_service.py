"""模型用量采集服务。

文本模型一般按输入/输出 token 计费，图片模型通常按模型、尺寸和张数计费。两种
计量方式不能混在一起估算。本模块通过 ``ContextVar`` 为一次 Celery 定时运行建立
独立账本，业务 Agent 无需感知监控细节，且并发任务之间不会串账。

金额不在这里硬编码：中转站的实际报价由客户账号决定，未配置价格表时只输出真实
调用量并明确标记“待账单核对”，避免展示伪精确成本。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextTokenUsage:
    """单次文本模型调用的上游 token 用量。"""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ImageGenerationUsage:
    """单次成功图片生成的计费维度。"""

    provider: str
    model: str
    size: str
    operation: str


@dataclass
class ModelUsageLedger:
    """一次业务运行的临时用量账本。"""

    scope: str
    text_usages: list[TextTokenUsage] = field(default_factory=list)
    image_usages: list[ImageGenerationUsage] = field(default_factory=list)


@dataclass(frozen=True)
class ModelUsageSummary:
    """供日志与后续数据库持久化消费的聚合结果。"""

    scope: str
    text_request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    image_request_count: int
    image_breakdown: tuple[str, ...]


_usage_ledger: ContextVar[ModelUsageLedger | None] = ContextVar(
    "model_usage_ledger",
    default=None,
)


def begin_model_usage_collection(scope: str) -> Token:
    """为当前协程/任务建立独立用量账本并返回恢复令牌。"""

    return _usage_ledger.set(ModelUsageLedger(scope=str(scope or "unknown")))


def end_model_usage_collection(token: Token) -> ModelUsageSummary:
    """汇总当前账本并恢复上级上下文，避免 Worker 复用线程时串账。"""

    ledger = _usage_ledger.get() or ModelUsageLedger(scope="unknown")
    _usage_ledger.reset(token)
    grouped_images: dict[tuple[str, str, str, str], int] = {}
    for item in ledger.image_usages:
        key = (item.provider, item.model, item.size, item.operation)
        grouped_images[key] = grouped_images.get(key, 0) + 1
    return ModelUsageSummary(
        scope=ledger.scope,
        text_request_count=len(ledger.text_usages),
        input_tokens=sum(item.input_tokens for item in ledger.text_usages),
        output_tokens=sum(item.output_tokens for item in ledger.text_usages),
        total_tokens=sum(item.total_tokens for item in ledger.text_usages),
        image_request_count=len(ledger.image_usages),
        image_breakdown=tuple(
            f"{provider}/{model}/{size}/{operation} x{count}"
            for (provider, model, size, operation), count in sorted(grouped_images.items())
        ),
    )


def record_text_token_usage(provider: str, model: str, usage: Any) -> None:
    """记录 OpenAI 兼容响应的真实 token 数；上游未返回 usage 时不猜测。"""

    ledger = _usage_ledger.get()
    if ledger is None or usage is None:
        return
    input_tokens = _read_usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _read_usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _read_usage_value(usage, "total_tokens")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return
    ledger.text_usages.append(TextTokenUsage(
        provider=str(provider),
        model=str(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    ))


def record_image_generation_usage(
    provider: str,
    model: str,
    size: str,
    *,
    has_reference_image: bool,
) -> None:
    """记录一次成功图像调用，不把图片调用伪装成 token 计费。"""

    ledger = _usage_ledger.get()
    if ledger is None:
        return
    ledger.image_usages.append(ImageGenerationUsage(
        provider=str(provider),
        model=str(model),
        size=str(size),
        operation="image_to_image" if has_reference_image else "text_to_image",
    ))


def _read_usage_value(usage: Any, *names: str) -> int:
    """兼容 SDK 对象与字典形式的 usage，异常字段统一按零处理。"""

    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            continue
    return 0
