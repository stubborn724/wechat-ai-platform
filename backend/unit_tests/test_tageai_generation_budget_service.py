"""微信公众号内容生成预算的行为测试。

这些测试只验证预算策略，不启动数据库、Celery 或真实模型。预算属于请求边界，必须在
任务入队前稳定拒绝危险输入，避免后续 Worker 已经调用模型后才发现规模不受控。
"""

import pytest

from app.services.tageai_generation_budget_service import (
    BudgetApprovalRequired,
    BudgetLimitExceeded,
    DEFAULT_GENERATION_BUDGET,
    normalize_generation_budget,
)


def test_missing_budget_uses_conservative_defaults():
    """未指定规模时使用公众号单篇默认预算，而不是读取平台历史的大配置上限。"""

    budget = normalize_generation_budget({})

    assert budget.article_character_limit == DEFAULT_GENERATION_BUDGET.article_character_limit
    assert budget.image_count == 5
    assert budget.video_count == 0
    assert budget.video_duration_seconds == 0


def test_exceeding_default_image_count_requires_one_time_approval():
    """超过默认配图数量必须先交互确认，不能由 Agent 静默放大任务。"""

    with pytest.raises(BudgetApprovalRequired) as exc_info:
        normalize_generation_budget({"image_count": 6})

    assert exc_info.value.code == "GENERATION_BUDGET_APPROVAL_REQUIRED"
    assert exc_info.value.requested["image_count"] == 6
    assert exc_info.value.default["image_count"] == 5


def test_approved_budget_is_limited_to_current_request():
    """一次性批准只放行当前请求，并且仍然不能超过服务端硬限制。"""

    budget = normalize_generation_budget(
        {"image_count": 6, "video_count": 1, "video_duration_seconds": 45},
        budget_approval={"image_count": 6, "video_count": 1, "video_duration_seconds": 45},
    )

    assert budget.image_count == 6
    assert budget.video_count == 1
    assert budget.video_duration_seconds == 45


def test_image_hard_limit_cannot_be_bypassed_by_approval():
    """用户确认不能突破绝对硬上限，避免费用和上下文不可控。"""

    with pytest.raises(BudgetLimitExceeded) as exc_info:
        normalize_generation_budget(
            {"image_count": 9},
            budget_approval={"image_count": 9},
        )

    assert exc_info.value.code == "GENERATION_BUDGET_HARD_LIMIT_EXCEEDED"
    assert exc_info.value.field == "image_count"


def test_video_duration_requires_approval_and_rejects_hard_limit():
    """视频默认关闭；启用或超过默认时长需要确认，超过绝对时长直接拒绝。"""

    with pytest.raises(BudgetApprovalRequired):
        normalize_generation_budget({"video_count": 1, "video_duration_seconds": 30})

    with pytest.raises(BudgetLimitExceeded) as exc_info:
        normalize_generation_budget(
            {"video_count": 1, "video_duration_seconds": 61},
            budget_approval={"video_count": 1, "video_duration_seconds": 61},
        )

    assert exc_info.value.field == "video_duration_seconds"


def test_invalid_negative_or_boolean_counts_are_rejected():
    """布尔值和负数不能被 Python 的整数兼容规则误认为合法数量。"""

    with pytest.raises(BudgetLimitExceeded):
        normalize_generation_budget({"image_count": -1})

    with pytest.raises(BudgetLimitExceeded):
        normalize_generation_budget({"video_count": True})
