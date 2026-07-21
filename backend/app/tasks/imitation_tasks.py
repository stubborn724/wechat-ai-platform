"""Celery tasks for scheduled imitation job execution."""

import asyncio
import logging
from datetime import date, datetime

from sqlalchemy import func

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import ImitationTask, ImitationTaskResult

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
def execute_imitation_task(self, task_id: int):
    """执行一个仿写任务（异步包装）"""
    db = MysqlSessionLocal()
    try:
        from app.services.imitation_service import execute_imitation_task as svc_execute

        result = asyncio.run(svc_execute(db, task_id))
        logger.info("Imitation task %d done: %s", task_id, result.get("generated", 0))
        return result
    except Exception as exc:
        logger.error("Imitation task %d failed: %s", task_id, exc)
        raise
    finally:
        db.close()


@celery_app.task
def poll_due_imitation_tasks():
    """每日定时任务：检查所有活跃仿写任务，到时间就执行

    Celery Beat 每小时调用一次，检查哪些任务需要执行。
    """
    db = MysqlSessionLocal()
    try:
        today = date.today()
        now = datetime.now()
        current_time = f"{now.hour:02d}:{now.minute:02d}"

        tasks = (
            db.query(ImitationTask)
            .filter(ImitationTask.status == "active")
            .all()
        )

        dispatched = []
        for task in tasks:
            # 检查日期范围
            if task.start_date and task.start_date.date() > today:
                continue
            if task.end_date and task.end_date.date() < today:
                continue

            # 检查当天是否已经执行过
            existing = (
                db.query(ImitationTaskResult)
                .filter(
                    ImitationTaskResult.task_id == task.id,
                    func.date(ImitationTaskResult.created_at) == today,
                )
                .first()
            )
            if existing:
                continue

            # 检查发布时间
            if task.publish_times:
                should_run = False
                for pub_time in task.publish_times:
                    if pub_time and pub_time <= current_time:
                        should_run = True
                        break
                if not should_run:
                    continue

            # 派发任务
            execute_imitation_task.delay(task.id)
            dispatched.append(task.id)
            logger.info("Dispatched imitation task %d (%s)", task.id, task.name)

        return {"checked": len(tasks), "dispatched": dispatched}

    except Exception as exc:
        logger.error("Poll imitation tasks failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()
