"""为绣蔓固定投喂源任务绑定已持久化的最新格式模板。

默认只输出检查结果。只有显式传入 ``--apply`` 才会更新数据库，防止在未知模板
质量或投喂源已变更时直接影响正式定时任务。脚本不创建模板、不修改 ERP 配图、
公众号账号或发布模式。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import MysqlSessionLocal
from app.models.mysql_models import ArticleFormatProfile, FeedSourceArticle, ScheduledTask
from app.services.xiuman_format_profile_binding_service import (
    build_xiuman_format_profile_binding_updates,
)


XIUMAN_TASK_IDS = (11, 13)
XIUMAN_SOURCE_ARTICLE_ID = 1


def parse_args() -> argparse.Namespace:
    """解析明确的执行开关，默认保持只读预览。"""

    parser = argparse.ArgumentParser(description="绑定绣蔓固定投喂源格式模板")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认写入任务格式模板绑定；未提供时仅预览",
    )
    return parser.parse_args()


def main() -> int:
    """检查最新模板并按需绑定两个绣蔓正式任务。"""

    args = parse_args()
    db = MysqlSessionLocal()
    try:
        source_article = (
            db.query(FeedSourceArticle)
            .filter(FeedSourceArticle.id == XIUMAN_SOURCE_ARTICLE_ID)
            .first()
        )
        if source_article is None:
            raise RuntimeError("绣蔓固定投喂文章不存在，已停止绑定")
        profile = (
            db.query(ArticleFormatProfile)
            .filter(
                ArticleFormatProfile.source_article_id == source_article.id,
                ArticleFormatProfile.is_active == True,
            )
            .order_by(ArticleFormatProfile.version.desc(), ArticleFormatProfile.id.desc())
            .first()
        )
        if profile is None:
            raise RuntimeError("绣蔓投喂文章没有可用格式模板，已停止绑定")
        tasks = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.id.in_(XIUMAN_TASK_IDS))
            .order_by(ScheduledTask.id.asc())
            .all()
        )
        updates = build_xiuman_format_profile_binding_updates(
            tasks,
            source_article_id=source_article.id,
            format_profile_id=profile.id,
        )
        if not updates:
            print(f"无需更新：模板 #{profile.id} 已绑定或任务投喂源不匹配")
            return 0
        for task, profile_id in updates:
            print(f"任务 #{task.id} {task.name}: format_profile_id -> {profile_id}")
        if not args.apply:
            print("预览完成，未写入数据库；质量验证通过后使用 --apply 执行")
            return 0
        for task, profile_id in updates:
            task.format_profile_id = profile_id
        db.commit()
        print(f"已绑定 {len(updates)} 个绣蔓任务到格式模板 #{profile.id}")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
