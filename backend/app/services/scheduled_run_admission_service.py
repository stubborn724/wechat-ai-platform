"""定时运行记录的受控并发准入策略。

定时任务原先按全局单队头串行，任意一个图片长任务都会让同一时段的其他品牌
等待十几分钟。该模块只决定哪些等待记录可以进入 Worker，不发送 Celery 消息、
不修改数据库，也不处理重试，从而可以在数据库锁内被稳定测试和复用。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar


RunType = TypeVar("RunType")


def select_admissible_scheduled_runs(
    runs: Sequence[RunType],
    *,
    max_active_runs: int,
    is_in_flight: Callable[[RunType], bool] | None = None,
) -> list[RunType]:
    """按稳定顺序选择可进入有限执行槽的等待记录。

    已派发、运行中和等待重试的记录都会占用一个全局执行槽，且占用其自身
    ``task_id``。因此即使仍有空闲槽，同一任务的下一时段也不能并发生成，避免
    ERP 防重、文章状态和多账号投递结果互相覆盖。
    """

    slot_limit = max(int(max_active_runs), 1)
    in_flight_predicate = is_in_flight or _is_default_in_flight
    ordered_runs = sorted(runs, key=_scheduled_run_sort_key)
    in_flight_runs = [run for run in ordered_runs if in_flight_predicate(run)]
    available_slots = max(slot_limit - len(in_flight_runs), 0)
    if available_slots == 0:
        return []

    reserved_task_ids = {
        int(getattr(run, "task_id", 0) or 0)
        for run in in_flight_runs
        if int(getattr(run, "task_id", 0) or 0) > 0
    }
    selected: list[RunType] = []
    for run in ordered_runs:
        if str(getattr(run, "status", "") or "").lower() != "queued":
            continue
        if in_flight_predicate(run):
            continue
        task_id = int(getattr(run, "task_id", 0) or 0)
        if task_id in reserved_task_ids:
            continue
        selected.append(run)
        reserved_task_ids.add(task_id)
        if len(selected) >= available_slots:
            break
    return selected


def _scheduled_run_sort_key(run: object) -> tuple[str, str, int]:
    """以计划日期、时间和记录 ID 建立跨任务的稳定公平顺序。"""

    return (
        str(getattr(run, "scheduled_date", "") or ""),
        str(getattr(run, "scheduled_time", "") or ""),
        int(getattr(run, "id", 0) or 0),
    )


def _is_default_in_flight(run: object) -> bool:
    """识别尚未完成的已派发记录，兼容纯函数测试中的最小对象。"""

    status = str(getattr(run, "status", "") or "").lower()
    if status in {"running", "retrying"}:
        return True
    if status != "queued":
        return False
    return bool(
        getattr(run, "celery_task_id", None)
        or getattr(run, "next_retry_at", None) is not None
    )
