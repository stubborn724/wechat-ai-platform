"""定时任务是否启用格式模板管线的隔离策略。"""

from __future__ import annotations


def should_use_format_profile(task: object) -> bool:
    """只接受已持久化绑定的模板，禁止执行器临时猜测版式。

    新任务的自动绑定发生在投喂源导入和任务保存阶段；执行器只读取最终外键。所有
    未设置 ``format_profile_id`` 的历史任务继续走原有分支，即使名称、文章来源或
    图片数与测试模板相似。
    """

    return bool(getattr(task, "format_profile_id", None))
