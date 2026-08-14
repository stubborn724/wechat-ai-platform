"""绣蔓固定投喂源格式模板绑定策略。

绣蔓 ERP 仿写的投喂文章长期固定，结构分析结果应以不可变格式模板复用。该模块
只判定哪些任务允许绑定，数据库读写交给迁移脚本，避免把一次性运维逻辑塞进
定时文章执行器并误影响其他品牌任务。
"""

from __future__ import annotations

from collections.abc import Iterable


def build_xiuman_format_profile_binding_updates(
    tasks: Iterable[object],
    *,
    source_article_id: int,
    format_profile_id: int,
) -> list[tuple[object, int]]:
    """返回可安全绑定格式模板的绣蔓任务及目标模板 ID。

    只有明确处于 feed 模式、并且 ``feed_article_ids`` 包含指定投喂文章的任务
    才能进入更新列表。名称不是安全边界，防止运营人员重命名后把模板错误绑定到
    不同投喂源；已经绑定相同模板的任务保持幂等，不产生无意义写入。
    """

    normalized_source_article_id = int(source_article_id)
    normalized_profile_id = int(format_profile_id)
    updates: list[tuple[object, int]] = []
    for task in tasks:
        if str(getattr(task, "writing_mode", "") or "").lower() != "feed":
            continue
        reference_ids = {
            int(article_id)
            for article_id in (getattr(task, "feed_article_ids", None) or [])
            if str(article_id).strip().isdigit()
        }
        if normalized_source_article_id not in reference_ids:
            continue
        if int(getattr(task, "format_profile_id", 0) or 0) == normalized_profile_id:
            continue
        updates.append((task, normalized_profile_id))
    return updates
