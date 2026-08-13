"""为四个原创品牌任务绑定可复用标题模板。

脚本只更新剪纸系列、写怀、中西无界和她格的精确任务名称，写入 ``style``
模板编号。它不读取或修改绣蔓、HTML 仿写、无缝海报测试等其他任务，可重复执行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import MysqlSessionLocal
from app.models.mysql_models import ScheduledTask
from app.services.writing_style_template_service import (
    JIANZHI_ARTFUL_LIVING_TEMPLATE_ID,
    SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
    XIEHUAI_ORIENTAL_LIVING_TEMPLATE_ID,
    ZHONGXIWUJIE_EAST_WEST_LIVING_TEMPLATE_ID,
)


TASK_STYLE_BY_NAME = {
    "中西无界-公域": ZHONGXIWUJIE_EAST_WEST_LIVING_TEMPLATE_ID,
    "中西无界-私域": ZHONGXIWUJIE_EAST_WEST_LIVING_TEMPLATE_ID,
    "写怀-公域": XIEHUAI_ORIENTAL_LIVING_TEMPLATE_ID,
    "写怀-私域": XIEHUAI_ORIENTAL_LIVING_TEMPLATE_ID,
    "剪纸系列-公域": JIANZHI_ARTFUL_LIVING_TEMPLATE_ID,
    "剪纸系列-私域": JIANZHI_ARTFUL_LIVING_TEMPLATE_ID,
    "她格原创-公域": SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
    "她格原创-私域": SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
}


def migrate_original_brand_title_templates() -> dict[str, str]:
    """按精确名称更新目标任务，缺失任务直接失败而不是误改其他记录。"""

    db = MysqlSessionLocal()
    try:
        tasks = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.name.in_(TASK_STYLE_BY_NAME))
            .all()
        )
        found_names = {task.name for task in tasks}
        missing_names = sorted(set(TASK_STYLE_BY_NAME) - found_names)
        if missing_names:
            raise RuntimeError("缺少原创品牌任务：" + "、".join(missing_names))

        for task in tasks:
            task.style = TASK_STYLE_BY_NAME[task.name]
        db.commit()
        return {task.name: task.style for task in tasks}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(migrate_original_brand_title_templates(), ensure_ascii=False, indent=2))
