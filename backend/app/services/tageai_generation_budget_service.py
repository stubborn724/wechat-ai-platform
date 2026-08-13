"""微信公众号内容生成预算校验。

该模块只负责请求边界，不负责调用模型或保存任务。把预算校验独立出来，是为了让
Agent 交互、HTTP 路由和 Celery Worker 共享同一套上限规则，避免某个入口绕过限制。
默认预算用于控制普通任务的成本；用户明确确认后可以临时提高，但绝对硬上限始终
由服务端执行，不能通过自然语言或客户端参数绕过。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class GenerationBudget:
    """当前一次请求冻结的生成预算。

    预算一旦创建就不应在 Worker 执行中被隐式放大。这样任务重试、桌面端重启和
    发布确认重放都会使用相同的资源边界，避免同一任务因入口不同产生不同成本。
    """

    article_character_limit: int = 5_000
    image_count: int = 5
    video_count: int = 0
    video_duration_seconds: int = 0
    image_prompt_character_limit: int = 600


DEFAULT_GENERATION_BUDGET = GenerationBudget()
HARD_GENERATION_BUDGET = GenerationBudget(
    article_character_limit=12_000,
    image_count=8,
    video_count=1,
    video_duration_seconds=60,
    image_prompt_character_limit=600,
)


class GenerationBudgetError(ValueError):
    """所有预算校验错误的公共基类，调用方可统一转换为结构化交互。"""

    code = "GENERATION_BUDGET_INVALID"


class BudgetApprovalRequired(GenerationBudgetError):
    """请求超过普通默认值，需要用户对当前任务做一次性确认。

    ``hard_limit`` 一并保存到异常，是为了让上层交互明确告知用户本次最多能批准到哪里；
    这只是展示信息，最终仍由本函数在收到批准值后再次执行绝对上限校验。
    """

    code = "GENERATION_BUDGET_APPROVAL_REQUIRED"

    def __init__(self, requested: dict[str, int], default: dict[str, int]):
        self.requested = requested
        self.default = default
        self.hard_limit = {
            field: getattr(HARD_GENERATION_BUDGET, field)
            for field in requested
        }
        fields = "、".join(f"{key}={value}" for key, value in requested.items())
        super().__init__(f"当前请求超过默认生成预算，需要用户确认：{fields}")


class BudgetLimitExceeded(GenerationBudgetError):
    """请求超过服务端绝对硬上限，任何用户确认都不能放行。"""

    code = "GENERATION_BUDGET_HARD_LIMIT_EXCEEDED"

    def __init__(self, field: str, value: object, maximum: int):
        self.field = field
        self.value = value
        self.maximum = maximum
        super().__init__(f"{field} 超过绝对上限 {maximum}")


def _read_non_negative_integer(payload: Mapping[str, object], field: str, default: int) -> int:
    """读取非负整数，显式拒绝 bool，避免 True 被 Python 当成 1 个任务。"""

    value = payload.get(field, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BudgetLimitExceeded(field, value, 0)
    return value


def _approval_covers(approval: Optional[Mapping[str, object]], field: str, requested: int) -> bool:
    """判断一次性批准是否覆盖当前字段，批准值不能小于实际请求值。"""

    if not approval:
        return False
    approved_value = approval.get(field)
    return isinstance(approved_value, int) and not isinstance(approved_value, bool) and approved_value >= requested


def normalize_generation_budget(
    payload: Optional[Mapping[str, object]],
    *,
    budget_approval: Optional[Mapping[str, object]] = None,
) -> GenerationBudget:
    """规范化并校验单次公众号生成预算。

    <p>默认超额会抛出 ``BudgetApprovalRequired``，由上层向用户展示一次确认；硬上限、
    负数、类型错误和视频参数不一致会抛出 ``BudgetLimitExceeded``。这里不接受一个泛化
    的 ``approved=True`` 开关，必须逐字段批准，防止客户端把确认扩大到未展示的资源。</p>
    """

    source = payload if isinstance(payload, Mapping) else {}
    requested = {
        "article_character_limit": _read_non_negative_integer(source, "article_character_limit", DEFAULT_GENERATION_BUDGET.article_character_limit),
        "image_count": _read_non_negative_integer(source, "image_count", DEFAULT_GENERATION_BUDGET.image_count),
        "video_count": _read_non_negative_integer(source, "video_count", DEFAULT_GENERATION_BUDGET.video_count),
        "video_duration_seconds": _read_non_negative_integer(source, "video_duration_seconds", DEFAULT_GENERATION_BUDGET.video_duration_seconds),
    }

    for field, value in requested.items():
        maximum = getattr(HARD_GENERATION_BUDGET, field)
        if value > maximum:
            raise BudgetLimitExceeded(field, value, maximum)

    if requested["video_count"] == 0 and requested["video_duration_seconds"] > 0:
        raise BudgetLimitExceeded("video_duration_seconds", requested["video_duration_seconds"], 0)

    over_default = {
        field: value
        for field, value in requested.items()
        if value > getattr(DEFAULT_GENERATION_BUDGET, field)
    }
    uncovered = {
        field: value
        for field, value in over_default.items()
        if not _approval_covers(budget_approval, field, value)
    }
    if uncovered:
        default = {field: getattr(DEFAULT_GENERATION_BUDGET, field) for field in uncovered}
        raise BudgetApprovalRequired(uncovered, default)

    # 视频开启但未指定时长，冻结一个适合公众号短视频的默认值，避免下游无限等待。
    video_duration = requested["video_duration_seconds"]
    if requested["video_count"] > 0 and video_duration == 0:
        video_duration = 30

    return GenerationBudget(
        article_character_limit=requested["article_character_limit"],
        image_count=requested["image_count"],
        video_count=requested["video_count"],
        video_duration_seconds=video_duration,
    )
