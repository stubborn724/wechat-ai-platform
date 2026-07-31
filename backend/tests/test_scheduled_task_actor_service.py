"""定时任务执行身份解析回归测试。

定时任务可以由历史脚本或管理入口创建，创建人字段不一定存在。文章表仍要求
有效用户外键，因此必须从当前租户成员中解析真实执行身份，不能写入 user_id=0。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块只测试纯决策函数，不访问真实业务数据库。"""

    yield


def test_prefers_configured_task_creator_when_the_creator_is_eligible() -> None:
    """正常创建的任务应持续归属原创建人，避免影响文章审计关系。"""

    from app.services.scheduled_task_actor_service import choose_task_actor_id

    assert choose_task_actor_id(configured_actor_id=12, eligible_actor_ids=[12, 24]) == 12


def test_falls_back_to_first_eligible_tenant_member_for_legacy_task() -> None:
    """历史任务缺少创建人时必须选择租户成员，不能产生 user_id=0。"""

    from app.services.scheduled_task_actor_service import choose_task_actor_id

    assert choose_task_actor_id(configured_actor_id=None, eligible_actor_ids=[24, 31]) == 24


def test_rejects_task_when_the_tenant_has_no_eligible_member() -> None:
    """无可用成员时停止任务，让错误可见且不触发外键失败。"""

    from app.services.scheduled_task_actor_service import (
        ScheduledTaskActorResolutionError,
        choose_task_actor_id,
    )

    with pytest.raises(ScheduledTaskActorResolutionError, match="没有可用成员"):
        choose_task_actor_id(configured_actor_id=None, eligible_actor_ids=[])
