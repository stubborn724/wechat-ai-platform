"""定时任务可恢复失败的领域异常。

本模块只表达“尚未产生公众号发布副作用、且等待后重新执行有意义”的失败。
调度器据此执行有限退避重试；微信发布结果不明确、群发额度耗尽、配置或参数
错误都不能使用这里的异常，避免把一次故障放大为重复发布。
"""

from __future__ import annotations


class RetryableScheduledTaskError(RuntimeError):
    """定时任务在发布前遇到临时故障时使用的基类。

    ``retryable`` 是显式领域标记，而不是依赖错误文本匹配。这样新入口只要在
    确认“没有外部发布副作用”后抛出该类型，就能复用调度器统一的 2/5/15 分钟
    有限重试策略，且不会误把本地配置错误纳入重试。
    """

    retryable = True


class RetryableImageQualityError(RetryableScheduledTaskError):
    """图片已生成但质量检查暂时无法读取对象时的可恢复失败。"""


class RetryableModelOutputError(RetryableScheduledTaskError):
    """模型已成功响应但结构化输出无法解析时的可恢复失败。"""
