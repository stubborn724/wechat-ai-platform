"""微信公众号平台级任务进度投影。

内部生成会拆成正文、图片和视频等多个子任务，但这些细节不能直接变成多个前端进度条。
本模块将真实子任务快照聚合成一个平台任务进度，供 Gateway、桌面端和未来多平台父任务
共同消费。计算过程是确定性的，避免 Agent 因上下文或重复调用产生不一致的百分比。
"""

from __future__ import annotations

import math
from collections.abc import Mapping


_PLATFORM_LABELS = {"wechat": "微信公众号"}
_IMAGE_SECONDS = {"min": 30, "max": 90}
_VIDEO_SECONDS = {"min": 120, "max": 600}
_TEXT_SECONDS = {"min": 30, "max": 180}


def _bounded_integer(snapshot: Mapping[str, object], field: str, default: int = 0) -> int:
    """读取非负计数；异常值回退为 0，避免状态投影因脏数据中断。"""

    value = snapshot.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _estimate_media_seconds(snapshot: Mapping[str, object], remaining: int) -> dict[str, int]:
    """按媒体类型和受控并发估算剩余区间，不承诺精确完成秒数。"""

    if remaining <= 0:
        return {"min": 0, "max": 0}
    video_remaining = _bounded_integer(snapshot, "video_remaining", 0)
    image_remaining = max(0, remaining - video_remaining)
    # 图片最多三个并行槽位；视频按一个槽位串行，取两者中更慢的部分。
    image_min = math.ceil(image_remaining / 3) * _IMAGE_SECONDS["min"] if image_remaining else 0
    image_max = math.ceil(image_remaining / 3) * _IMAGE_SECONDS["max"] if image_remaining else 0
    video_min = video_remaining * _VIDEO_SECONDS["min"]
    video_max = video_remaining * _VIDEO_SECONDS["max"]
    return {"min": max(image_min, video_min), "max": max(image_max, video_max)}


def project_platform_progress(snapshot: Mapping[str, object]) -> dict:
    """将内部阶段快照转换为单个平台的公开进度。

    进度权重固定为正文 40%、媒体 50%、发布 10%。在用户确认前，`READY_FOR_PUBLISH`
    保持 95%，避免用户看到 100% 后误以为已经发布；真正发布成功才返回 100%。缺失或
    异常字段按保守值处理，失败诊断仍通过结构化错误返回。
    """

    platform = str(snapshot.get("platform") or "wechat").strip().lower()
    platform_label = _PLATFORM_LABELS.get(platform, platform)
    stage = str(snapshot.get("stage") or "QUEUED").strip().upper()
    media_total = _bounded_integer(snapshot, "media_total")
    media_ready = min(_bounded_integer(snapshot, "media_ready"), media_total)
    media_failed = min(_bounded_integer(snapshot, "media_failed"), media_total - media_ready)
    media_generating = _bounded_integer(snapshot, "media_generating")
    remaining = max(0, media_total - media_ready - media_failed)
    text_progress = min(100, _bounded_integer(snapshot, "text_progress"))

    media_summary = {
        "total": media_total,
        "ready": media_ready,
        "generating": media_generating,
        "failed": media_failed,
    }

    if _bounded_integer(snapshot, "required_media_failed") > 0:
        return {
            "platform": platform,
            "platformLabel": platform_label,
            "status": "FAILED",
            "stage": "MEDIA_FAILED",
            "progress": min(95, 40 + round((media_ready / media_total) * 50)) if media_total else 40,
            "mediaSummary": media_summary,
            "estimatedRemainingSeconds": {"min": 0, "max": 0},
            "error": {"code": "REQUIRED_MEDIA_FAILED", "message": "必需图片或视频生成失败"},
        }

    if stage == "TEXT_GENERATING":
        progress = round(text_progress * 0.4)
        estimate = _TEXT_SECONDS
        status = "TEXT_GENERATING"
    elif stage == "TEXT_READY":
        progress = 40
        estimate = _estimate_media_seconds(snapshot, remaining)
        status = "TEXT_READY"
    elif stage == "MEDIA_GENERATING":
        media_ratio = media_ready / media_total if media_total else 1
        progress = 40 + round(media_ratio * 50)
        estimate = _estimate_media_seconds(snapshot, remaining)
        status = "MEDIA_GENERATING"
    elif stage == "READY_FOR_PUBLISH":
        progress = 95
        estimate = {"min": 0, "max": 0}
        status = "READY_FOR_PUBLISH"
    elif stage == "PUBLISHING":
        progress = 99
        estimate = {"min": 15, "max": 120}
        status = "PUBLISHING"
    elif stage == "PUBLISHED":
        progress = 100
        estimate = {"min": 0, "max": 0}
        status = "PUBLISHED"
    elif stage in {"FAILED", "CANCELLED"}:
        progress = min(100, _bounded_integer(snapshot, "progress"))
        estimate = {"min": 0, "max": 0}
        status = stage
    else:
        progress = min(39, _bounded_integer(snapshot, "progress"))
        estimate = _TEXT_SECONDS
        status = "QUEUED"

    return {
        "platform": platform,
        "platformLabel": platform_label,
        "status": status,
        "stage": stage,
        "progress": max(0, min(100, progress)),
        "mediaSummary": media_summary,
        "estimatedRemainingSeconds": estimate,
    }

