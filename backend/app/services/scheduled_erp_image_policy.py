"""ERP 分类配图定时任务的纯策略能力。

此模块只处理时间窗口和候选图片筛选，不依赖数据库、HTTP 或 Celery，
让“每天多次触发”和“三天内不重复”两条关键业务规则可以稳定测试。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TypeVar


class ErpImageSelectionError(ValueError):
    """ERP 分类中没有足够的未使用图片时抛出。

    显式失败优于重复素材，因为重复会直接违背任务对近三天内容差异化的约束。
    """


ProductT = TypeVar("ProductT")


def find_due_schedule_times(
    publish_times: Iterable[str],
    now: datetime,
    completed_schedule_times: set[str],
    grace_minutes: int = 5,
) -> list[str]:
    """返回当前检查窗口内尚未创建执行记录的时段。

    调度器会按分钟或五分钟轮询；允许一个短宽限窗口可避免轮询稍晚导致任务漏发，
    同时避免服务在长时间中断后把当天已错过的多个时间段集中补发。
    """
    if grace_minutes < 0:
        raise ValueError("grace_minutes 不能小于 0")

    current_minutes = now.hour * 60 + now.minute
    due_times: list[str] = []
    for schedule_time in sorted(set(publish_times)):
        try:
            hour_text, minute_text = schedule_time.split(":", maxsplit=1)
            scheduled_minutes = int(hour_text) * 60 + int(minute_text)
        except (ValueError, AttributeError):
            # 配置保存时会校验格式；此处容错保证一条坏配置不会阻塞其他任务。
            continue

        if not 0 <= scheduled_minutes < 24 * 60:
            continue
        if schedule_time in completed_schedule_times:
            continue
        if 0 <= current_minutes - scheduled_minutes <= grace_minutes:
            due_times.append(schedule_time)
    return due_times


def select_unused_erp_products(
    candidates: Iterable[ProductT],
    recent_image_urls: set[str],
    requested_count: int,
    shuffle: Callable[[list[ProductT]], None] | None = None,
) -> list[ProductT]:
    """从同一 ERP 分类候选中随机选择未在窗口期使用过的图片。

    通过图片 URL 去重而非产品名去重，因为同一产品可能有多个命名或多个系列字段，
    但同一远端图片在公众号中仍应被视为重复素材。
    """
    if requested_count < 1:
        raise ValueError("requested_count 必须至少为 1")

    eligible: list[ProductT] = []
    seen_urls: set[str] = set()
    for product in candidates:
        image_url = str(getattr(product, "image_url", "") or "").strip()
        if not image_url or image_url in recent_image_urls or image_url in seen_urls:
            continue
        seen_urls.add(image_url)
        eligible.append(product)

    if len(eligible) < requested_count:
        raise ErpImageSelectionError(
            f"ERP 分类可用图片不足：需要 {requested_count} 张，但三天内未重复的图片只有 {len(eligible)} 张"
        )

    if shuffle is None:
        import random

        random.SystemRandom().shuffle(eligible)
    else:
        shuffle(eligible)
    return eligible[:requested_count]
