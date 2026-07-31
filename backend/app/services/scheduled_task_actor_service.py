"""定时任务的执行身份解析服务。

定时任务是长期存在的后台配置，可能来自早期脚本、数据迁移或已删除用户，不能假设
``created_by`` 一定有效。该模块只负责从任务创建人和租户成员候选中给出合法用户 ID，
让文章落库、模型调用审计及公众号交付使用同一身份。
"""

from __future__ import annotations

from collections.abc import Iterable


class ScheduledTaskActorResolutionError(RuntimeError):
    """任务无法关联有效租户成员时抛出，阻止产生非法文章外键。"""


def choose_task_actor_id(
    configured_actor_id: int | None,
    eligible_actor_ids: Iterable[int],
) -> int:
    """在已校验的租户成员中确定后台任务的实际执行人。

    优先保持任务原创建人的审计归属；只有创建人缺失或已经不属于当前有效成员集合时，
    才使用排序后的第一个可用成员作为历史任务兼容兜底。没有候选成员必须显式失败，
    因为 ``0`` 不是用户表的合法外键，继续执行会在文章创建阶段留下难以诊断的错误。
    """

    eligible_ids = sorted({int(actor_id) for actor_id in eligible_actor_ids if actor_id})
    normalized_configured_id = int(configured_actor_id) if configured_actor_id else None

    if normalized_configured_id in eligible_ids:
        return normalized_configured_id
    if eligible_ids:
        return eligible_ids[0]
    raise ScheduledTaskActorResolutionError("定时任务所属租户没有可用成员，已停止生成")


def resolve_scheduled_task_actor_id(db, task) -> int:
    """查询任务所属租户的有效成员，并解析一次可复用的执行人 ID。

    数据库访问收敛在这里，调用方只获得整数 ID，避免执行器的多个阶段各自使用
    ``task.created_by or 0`` 而出现文章归属、发布审计不一致的问题。
    """

    from app.models.mysql_models import Membership, User

    rows = (
        db.query(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .filter(
            Membership.tenant_id == task.tenant_id,
            Membership.is_active == True,
            User.is_active == True,
        )
        .order_by(Membership.user_id.asc())
        .all()
    )
    return choose_task_actor_id(task.created_by, (row[0] for row in rows))
