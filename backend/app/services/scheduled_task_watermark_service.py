"""定时任务水印快照的规范化服务。

租户水印设置是“当前全局配置”，而定时任务需要在创建或明确更新时锁定一份
自己的配置快照。这个模块只负责校验、清洗和补齐快照字段，不负责下载 Logo 或
绘制图片，从而让 API、数据库迁移和图片归档链路共享同一份配置契约。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SUPPORTED_TYPES = {"text", "logo"}
_SUPPORTED_POSITIONS = {
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
}
_DEFAULT_TEXT = "绣蔓家具 TEL:18682130473"


def normalize_task_watermark_config(
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """规范化一份任务级水印快照，并拒绝无法可靠渲染的配置。

    ``None`` 表示任务没有快照，发布链路应继续沿用历史的租户全局配置；一旦
    传入字典，即使 ``enabled`` 为 ``False`` 也表示任务明确覆盖全局设置。这样
    才能实现“这个任务关闭水印、其他任务仍跟随全局”的隔离效果。

    返回值只保留渲染器需要的字段，避免把前端临时字段或未来未知字段直接写入
    JSON 列。数值范围在这里收口，防止异常配置在 Worker 中才暴露出来。
    """

    if config is None:
        return None
    if not isinstance(config, Mapping):
        raise ValueError("watermark_config 必须是对象")

    watermark_type = str(config.get("type", "text") or "text").strip().lower()
    if watermark_type not in _SUPPORTED_TYPES:
        raise ValueError("watermark_config.type 必须是 text 或 logo")

    enabled = bool(config.get("enabled", True))
    position = str(config.get("position", "bottom-right") or "bottom-right").strip()
    if position not in _SUPPORTED_POSITIONS:
        raise ValueError("watermark_config.position 不受支持")

    font_size = _parse_number(config.get("font_size", 24), "font_size", 8, 96)
    opacity = _parse_float(config.get("opacity", 0.9), "opacity", 0.0, 1.0)
    margin = _parse_number(config.get("margin", 40), "margin", 0, 512)
    locked = bool(config.get("locked", True))

    normalized: dict[str, Any] = {
        "enabled": enabled,
        "type": watermark_type,
        "font_size": font_size,
        "position": position,
        "opacity": opacity,
        "margin": margin,
        "locked": locked,
    }

    if watermark_type == "text":
        content = " ".join(str(config.get("content", "") or "").split())[:128]
        if enabled and not content:
            raise ValueError("文字水印启用时必须提供 content")
        # 关闭状态仍保留默认内容，方便 UI 展示和后续重新开启；当前任务的固定
        # 快照显式写入实际文字，因此不会因为租户全局配置变化而换内容。
        normalized["content"] = content or _DEFAULT_TEXT
    else:
        image_key = str(config.get("image_key", "") or "").strip()
        if enabled and not image_key:
            raise ValueError("Logo 水印启用时必须提供 image_key")
        normalized["image_key"] = image_key
        normalized["scale"] = _parse_float(config.get("scale", 0.15), "scale", 0.01, 1.0)

    return normalized


def _parse_number(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    """解析整数配置并检查范围，避免 bool 被当成合法字号或边距。"""

    if isinstance(value, bool):
        raise ValueError(f"watermark_config.{field_name} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"watermark_config.{field_name} 必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"watermark_config.{field_name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return parsed


def _parse_float(value: Any, field_name: str, minimum: float, maximum: float) -> float:
    """解析比例配置并限制在渲染器可接受的范围内。"""

    if isinstance(value, bool):
        raise ValueError(f"watermark_config.{field_name} 必须是数字")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"watermark_config.{field_name} 必须是数字") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"watermark_config.{field_name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return parsed
