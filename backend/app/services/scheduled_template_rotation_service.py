"""定时任务来源模板轮换的领域服务。

本模块将“轮换配置是否合法”和“某个已排定时段应该使用哪个模板”集中处理。
执行器只保存结果并消费运行快照，因此 Celery 重试、Worker 重启或后续编辑任务
都不会重新推导已经排队时段的模板。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal, Mapping


RotationBasis = Literal["publish_day", "publish_run"]
_VALID_ROTATION_BASES: tuple[RotationBasis, ...] = ("publish_day", "publish_run")


@dataclass(frozen=True)
class TemplateRotationConfig:
    """已规范化的模板轮换配置。

    ``profile_ids`` 的顺序就是运营人员在页面中配置的审美轮换顺序。``basis``
    决定一次轮换单位是一个自然发布日还是一个具体发布时间，
    ``uses_per_template`` 则决定每个模板连续占用多少个单位。
    """

    profile_ids: tuple[int, ...]
    basis: RotationBasis
    uses_per_template: int

    def to_storage_dict(self) -> dict[str, object]:
        """转换为稳定 JSON，避免不同入口写出不同形态的配置。"""

        return {
            "enabled": True,
            "profile_ids": list(self.profile_ids),
            "basis": self.basis,
            "uses_per_template": self.uses_per_template,
        }


def normalize_template_rotation_config(value: object) -> TemplateRotationConfig | None:
    """解析 API/数据库 JSON，并将关闭状态统一为 ``None``。

    历史任务没有此字段时同样返回 ``None``，调用方自然走原有单模板路径。启用
    轮换时在保存前严格校验，避免值不完整直到定时 Worker 执行才暴露问题。
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("模板轮换配置必须是对象")
    if not bool(value.get("enabled", False)):
        return None

    profile_ids = _normalize_profile_ids(value.get("profile_ids"))
    if len(profile_ids) < 2:
        raise ValueError("启用模板轮换时至少选择 2 个来源模板")
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("模板轮换中的来源模板不能重复")

    basis = str(value.get("basis") or "publish_day")
    if basis not in _VALID_ROTATION_BASES:
        raise ValueError("模板轮换依据只能是 publish_day 或 publish_run")

    uses_per_template = _normalize_positive_integer(
        value.get("uses_per_template", 1),
        field_name="连续使用次数",
    )
    return TemplateRotationConfig(
        profile_ids=tuple(profile_ids),
        basis=basis,  # type: ignore[arg-type]
        uses_per_template=uses_per_template,
    )


def select_rotation_profile_id(
    config: TemplateRotationConfig,
    occurrence_index: int,
) -> int:
    """按已发生的轮换单位数确定当前应使用的模板 ID。

    ``occurrence_index`` 从零开始。例如按发布日轮换时，首个有时段的日期为 0；
    同一天多个时段共用 0。通过只依赖持久化的排期序号，失败和重试不会改变结果。
    """

    if occurrence_index < 0:
        raise ValueError("轮换序号不能小于 0")
    profile_index = (occurrence_index // config.uses_per_template) % len(
        config.profile_ids
    )
    return config.profile_ids[profile_index]


def validate_rotation_profiles(
    profiles: Iterable[object],
    *,
    expected_profile_ids: Iterable[int],
) -> None:
    """验证轮换引用的模板均属于来源文章且已由上层过滤租户/启用状态。

    此函数不查询数据库，便于 API 使用单次批量查询后校验；运行时也可以复用它
    防止数据库被人工修改后将无来源模板纳入轮换。
    """

    expected_ids = list(expected_profile_ids)
    by_id = {int(getattr(profile, "id", 0) or 0): profile for profile in profiles}
    missing_ids = [profile_id for profile_id in expected_ids if profile_id not in by_id]
    if missing_ids:
        raise ValueError("轮换模板不存在、已停用或不属于当前租户")
    invalid_ids = [
        profile_id
        for profile_id in expected_ids
        if not getattr(by_id[profile_id], "source_article_id", None)
    ]
    if invalid_ids:
        raise ValueError("轮换只能选择来自投喂文章的来源模板")


def resolve_rotation_profile_for_scheduled_slot(
    db,
    *,
    task: object,
    scheduled_date: date,
    scheduled_time: str,
) -> tuple[int | None, int | None]:
    """为一个即将创建的运行记录确定模板快照和配置版本。

    按发布日时只统计更早的不同日期，因此同一天早中晚时段得到同一个模板。
    按发布次数时统计更早的日期与时间组合。统计的是已创建的排期而非成功结果：
    排队、失败重试和服务重启后都保持原有的审美顺序，不会因为偶发失败跳号。
    """

    config = normalize_template_rotation_config(
        getattr(task, "template_rotation_config", None)
    )
    if config is None:
        return None, None

    from sqlalchemy import and_, or_

    from app.models.mysql_models import ScheduledTaskRun

    rotation_version = int(
        getattr(task, "template_rotation_version", 0) or 0
    )
    base_query = db.query(ScheduledTaskRun).filter(
        ScheduledTaskRun.task_id == int(getattr(task, "id")),
        ScheduledTaskRun.template_rotation_version == rotation_version,
        ScheduledTaskRun.format_profile_id.isnot(None),
    )
    if config.basis == "publish_day":
        earlier_dates = (
            base_query.filter(ScheduledTaskRun.scheduled_date < scheduled_date)
            .with_entities(ScheduledTaskRun.scheduled_date)
            .distinct()
            .all()
        )
        occurrence_index = len(earlier_dates)
    else:
        occurrence_index = base_query.filter(
            or_(
                ScheduledTaskRun.scheduled_date < scheduled_date,
                and_(
                    ScheduledTaskRun.scheduled_date == scheduled_date,
                    ScheduledTaskRun.scheduled_time < scheduled_time,
                ),
            )
        ).count()

    return select_rotation_profile_id(config, occurrence_index), rotation_version


def _normalize_profile_ids(value: object) -> list[int]:
    """保留页面配置顺序，并拒绝空值和非正数 ID。"""

    if not isinstance(value, (list, tuple)):
        raise ValueError("模板轮换必须提供来源模板列表")
    normalized: list[int] = []
    for raw_id in value:
        if isinstance(raw_id, bool):
            raise ValueError("来源模板 ID 必须是正整数")
        try:
            profile_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("来源模板 ID 必须是正整数") from exc
        if profile_id <= 0:
            raise ValueError("来源模板 ID 必须是正整数")
        normalized.append(profile_id)
    return normalized


def _normalize_positive_integer(value: object, *, field_name: str) -> int:
    """转换 JSON 数值并拦截布尔值、零和负数。"""

    if isinstance(value, bool):
        raise ValueError(f"{field_name}必须是正整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是正整数") from exc
    if normalized <= 0:
        raise ValueError(f"{field_name}必须是正整数")
    return normalized
