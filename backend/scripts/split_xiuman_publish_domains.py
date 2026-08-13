"""把“绣蔓仿写”拆成公域和私域两个定时任务。

脚本只复制任务配置，不复制历史运行记录和已生成文章。这样历史数据仍归属于
原任务，新的 08:00/20:00 私域与 13:00 公域则各自形成独立的队列幂等边界。
脚本默认针对当前正式任务 ID 11，并通过名称和租户做幂等判断，重复执行不会
不断创建新任务。
"""

from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path

from sqlalchemy import inspect

# 直接执行 scripts 下的文件时补齐 backend 根目录。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask, ScheduledTaskSlot
from app.services.publish_domain_policy import (
    PRIVATE_PUBLISH_DOMAIN,
    PUBLIC_PUBLISH_DOMAIN,
)


logger = logging.getLogger(__name__)

SOURCE_TASK_ID = 11
PRIVATE_TASK_NAME = "绣蔓仿写-私域"
PUBLIC_TASK_NAME = "绣蔓仿写-公域"


def _copy_task_config(source: ScheduledTask) -> dict:
    """复制任务的业务配置，排除 ID、统计和时间戳等运行态字段。"""

    runtime_fields = {
        "id",
        "created_at",
        "updated_at",
        "last_run_at",
        "total_generated",
    }
    column_names = {
        column.key for column in inspect(ScheduledTask).mapper.column_attrs
    }
    return {
        name: copy.deepcopy(getattr(source, name))
        for name in column_names - runtime_fields
    }


def _copy_slots(db, source_id: int, target_id: int, domain: str) -> None:
    """复制旧槽位的内容类型，并把槽位域同步为目标任务域。"""

    source_slots = (
        db.query(ScheduledTaskSlot)
        .filter(ScheduledTaskSlot.task_id == source_id)
        .order_by(ScheduledTaskSlot.sort_order)
        .all()
    )
    db.query(ScheduledTaskSlot).filter(ScheduledTaskSlot.task_id == target_id).delete()
    for slot in source_slots:
        db.add(
            ScheduledTaskSlot(
                task_id=target_id,
                sort_order=slot.sort_order,
                content_type=slot.content_type,
                publish_domain=domain,
            )
        )


def split_task() -> tuple[int, int]:
    """执行拆分并返回私域、公域任务 ID。"""

    db = MysqlSessionLocal()
    try:
        source = db.query(ScheduledTask).filter(ScheduledTask.id == SOURCE_TASK_ID).first()
        if source is None:
            raise RuntimeError(f"找不到源任务 id={SOURCE_TASK_ID}")

        # 先查找已有公域副本；没有时以当前源任务配置创建，确保水印、格式和
        # 投喂源等字段全部保持一致。源任务本身改为私域并保留历史运行记录。
        public_task = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.tenant_id == source.tenant_id,
                ScheduledTask.name == PUBLIC_TASK_NAME,
            )
            .first()
        )
        if public_task is None:
            public_task = ScheduledTask(**_copy_task_config(source))
            db.add(public_task)
            db.flush()
            _copy_slots(db, source.id, public_task.id, PUBLIC_PUBLISH_DOMAIN)

        source.name = PRIVATE_TASK_NAME
        source.publish_times = ["08:00", "20:00"]
        source.publish_domain = PRIVATE_PUBLISH_DOMAIN
        public_task.name = PUBLIC_TASK_NAME
        public_task.publish_times = ["13:00"]
        public_task.publish_domain = PUBLIC_PUBLISH_DOMAIN
        if public_task.id != source.id:
            public_task.total_generated = public_task.total_generated or 0
            public_task.last_run_at = public_task.last_run_at

        # 已存在副本时也同步槽位域，避免历史手工编辑留下相反域配置。
        _copy_slots(db, source.id, source.id, PRIVATE_PUBLISH_DOMAIN)
        _copy_slots(db, source.id, public_task.id, PUBLIC_PUBLISH_DOMAIN)
        db.commit()
        logger.info(
            "已拆分任务：私域 task_id=%s，公域 task_id=%s",
            source.id,
            public_task.id,
        )
        return source.id, public_task.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    split_task()
