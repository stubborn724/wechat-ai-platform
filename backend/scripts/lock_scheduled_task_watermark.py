"""为指定定时任务写入固定水印快照。

这是一次可重复执行的运维脚本，不把某个任务 ID 硬编码到业务发布代码中。脚本
会先核对任务名称，避免把固定样式误写到同租户的其他任务；重复执行只会把同一份
规范化快照再次写入，不会创建文章或触发任务。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 直接执行 scripts 下的文件时，Python 默认不会把 backend 根目录加入 sys.path。
# 根据脚本位置补齐模块路径，保持运维命令与其他脚本一致。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask
from app.services.scheduled_task_watermark_service import (
    normalize_task_watermark_config,
)


logger = logging.getLogger(__name__)

TASK_WATERMARK_CONFIG = {
    "enabled": True,
    "type": "text",
    "content": "绣蔓家具 TEL:18682130473",
    "font_size": 24,
    "position": "bottom-right",
    "locked": True,
}


def lock_task_watermark(task_id: int, expected_name: str) -> dict:
    """按任务 ID 和名称写入固定快照，并返回核对结果。"""

    normalized_config = normalize_task_watermark_config(TASK_WATERMARK_CONFIG)
    db = MysqlSessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if task is None:
            raise RuntimeError(f"定时任务不存在: id={task_id}")
        if task.name != expected_name:
            raise RuntimeError(
                f"任务名称不匹配: id={task_id}, 实际={task.name!r}, 期望={expected_name!r}"
            )

        task.enable_watermark = True
        task.watermark_config = normalized_config
        db.commit()
        db.refresh(task)
        logger.info(
            "已固定任务水印: id=%s, name=%s, config=%s",
            task.id,
            task.name,
            task.watermark_config,
        )
        return {
            "id": task.id,
            "name": task.name,
            "enable_watermark": bool(task.enable_watermark),
            "watermark_config": task.watermark_config,
        }
    finally:
        db.close()


def main() -> None:
    """解析运维参数并执行一次幂等配置更新。"""

    parser = argparse.ArgumentParser(description="固定定时任务水印")
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--expected-name", required=True)
    args = parser.parse_args()
    result = lock_task_watermark(args.task_id, args.expected_name)
    print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
