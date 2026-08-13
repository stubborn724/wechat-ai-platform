"""定时任务发布版式的显式选择策略。

本模块只负责回答一个边界问题：当前定时任务是否明确允许进入纯海报流程。
版式规则来自知识库，但知识库本身不能改变历史任务的行为；否则用户给旧任务
增加一份海报规范后，旧任务可能在没有编辑任务配置的情况下突然换输出格式。
将判断抽成无数据库的纯函数，执行器和单元测试都使用同一份契约。
"""

from __future__ import annotations

from typing import Any


STANDARD_LAYOUT_MODE = "standard"
SEAMLESS_POSTER_LAYOUT_MODE = "seamless_poster"


def should_use_poster_layout(layout_mode: str | None, publication_profile: Any) -> bool:
    """判断定时任务是否可以进入无缝海报生成链路。

    必须同时满足两个条件：任务明确选择 ``seamless_poster``，且所选知识库的
    发布格式确实识别为海报。缺少任一条件都回到普通文章流程，避免旧任务被
    知识库内容的偶然关键词污染；执行器对显式海报但规则不匹配的情况会单独
    报错，而不会静默生成另一种格式。
    """

    normalized_mode = str(layout_mode or STANDARD_LAYOUT_MODE).strip().lower()
    return (
        normalized_mode == SEAMLESS_POSTER_LAYOUT_MODE
        and bool(getattr(publication_profile, "is_poster_gallery", False))
    )
