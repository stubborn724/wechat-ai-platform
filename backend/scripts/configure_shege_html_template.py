"""创建她格 HTML 图文模板投喂源并绑定现有她格定时任务。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask
from app.services.shege_html_template_service import (
    build_shege_template_task_patch,
    ensure_shege_html_template_binding,
)
from app.services.writing_style_template_service import (
    SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
)


SHEGE_TASK_NAMES = (
    "她格原创-公域",
    "她格原创-私域",
    "她格用绣蔓测试",
)


def configure_shege_html_template() -> dict[str, object]:
    """以显式白名单绑定她格任务，拒绝修改其他品牌或绣蔓任务。"""

    db = MysqlSessionLocal()
    try:
        tasks = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.name.in_(SHEGE_TASK_NAMES))
            .order_by(ScheduledTask.id)
            .all()
        )
        found_names = {task.name for task in tasks}
        missing_names = sorted(set(SHEGE_TASK_NAMES) - found_names)
        if missing_names:
            raise RuntimeError(f"缺少她格任务，停止绑定：{', '.join(missing_names)}")
        tenant_ids = {int(task.tenant_id) for task in tasks}
        if len(tenant_ids) != 1:
            raise RuntimeError("她格任务不属于同一租户，停止绑定")
        for task in tasks:
            if task.style != SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID:
                raise RuntimeError(f"任务“{task.name}”不是她格原创任务，停止绑定")

        binding = ensure_shege_html_template_binding(
            db,
            tenant_id=tenant_ids.pop(),
        )
        patch = build_shege_template_task_patch(
            feed_source_id=binding.feed_source_id,
            feed_article_id=binding.feed_article_id,
            format_profile_id=binding.format_profile_id,
        )
        for task in tasks:
            for field_name, value in patch.items():
                setattr(task, field_name, value)
        db.commit()
        return {
            "feed_source_id": binding.feed_source_id,
            "feed_article_id": binding.feed_article_id,
            "format_profile_id": binding.format_profile_id,
            "task_ids": {task.name: int(task.id) for task in tasks},
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    """执行配置并输出不包含密钥的验证摘要。"""

    print(json.dumps(configure_shege_html_template(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
