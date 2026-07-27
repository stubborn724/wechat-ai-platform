"""优化效果比较服务 — 按相同观察窗口对比原文和优化版的阅读指标"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import Article, ArticleMetrics, ArticleOptimization

logger = logging.getLogger(__name__)

# 最小观察周期（小时）
MIN_OBSERVATION_HOURS = 72
# 最低阅读样本量
MIN_READ_SAMPLE = 100


class OptimizationComparisonService:
    """优化效果比较"""

    def compare(self, db: Session, optimization_id: int) -> dict:
        """比较原文和优化版的阅读表现"""
        opt = db.query(ArticleOptimization).filter(
            ArticleOptimization.id == optimization_id
        ).first()
        if not opt:
            raise ValueError(f"Optimization record {optimization_id} not found")

        source = db.query(Article).filter(Article.id == opt.source_article_id).first()
        optimized = db.query(Article).filter(Article.id == opt.optimized_article_id).first()
        if not source or not optimized:
            raise ValueError("Source or optimized article not found")

        # 确定观察窗口：取两者都有的指标日期交集
        source_metrics = self._get_metrics_since(db, source.id, source.created_at)
        opt_metrics = self._get_metrics_since(db, optimized.id, optimized.created_at)

        # 计算观察时长（小时）
        observation_hours = None
        if optimized.wechat_publish_time:
            observation_hours = (datetime.utcnow() - optimized.wechat_publish_time).total_seconds() / 3600
        elif optimized.created_at:
            observation_hours = (datetime.utcnow() - optimized.created_at).total_seconds() / 3600

        result = {
            "optimization_id": optimization_id,
            "observation_hours": observation_hours,
            "sample_sufficient": False,
            "comparable": False,
            "result": "inconclusive",
        }

        if observation_hours and observation_hours < MIN_OBSERVATION_HOURS:
            result["result"] = "observing"
            result["note"] = f"观察中（{observation_hours:.0f}/{MIN_OBSERVATION_HOURS} 小时）"
            return result

        # 对比指标（取各自最近的指标快照）
        s_latest = source_metrics[0] if source_metrics else None
        o_latest = opt_metrics[0] if opt_metrics else None

        if not s_latest or not o_latest:
            result["result"] = "inconclusive"
            result["note"] = "缺少指标数据"
            return result

        s_read = s_latest.read_count or 0
        o_read = o_latest.read_count or 0

        if s_read < MIN_READ_SAMPLE and o_read < MIN_READ_SAMPLE:
            result["result"] = "inconclusive"
            result["note"] = f"样本不足（原文={s_read}，优化={o_read}）"
            return result

        # 计算变化率
        def _rate_change(old_val, new_val):
            if old_val == 0:
                return 1.0 if new_val > 0 else 0.0
            return (new_val - old_val) / old_val

        read_change = _rate_change(s_read, o_read)
        like_rate_s = (s_latest.like_count or 0) / max(s_read, 1)
        like_rate_o = (o_latest.like_count or 0) / max(o_read, 1)
        share_rate_s = (s_latest.share_count or 0) / max(s_read, 1)
        share_rate_o = (o_latest.share_count or 0) / max(o_read, 1)

        result["sample_sufficient"] = True
        result["comparable"] = True
        result["source_read_count"] = s_read
        result["optimized_read_count"] = o_read
        result["read_change_rate"] = round(read_change, 4)
        result["source_like_rate"] = round(like_rate_s, 4)
        result["optimized_like_rate"] = round(like_rate_o, 4)
        result["source_share_rate"] = round(share_rate_s, 4)
        result["optimized_share_rate"] = round(share_rate_o, 4)

        # 判定效果
        improvements = 0
        declines = 0
        if read_change > 0.1:
            improvements += 1
        elif read_change < -0.1:
            declines += 1
        if like_rate_o > like_rate_s * 1.1:
            improvements += 1
        elif like_rate_o < like_rate_s * 0.9:
            declines += 1
        if share_rate_o > share_rate_s * 1.1:
            improvements += 1
        elif share_rate_o < share_rate_s * 0.9:
            declines += 1

        if improvements >= 2:
            result["result"] = "effective"
            result["note"] = "优化效果明显"
        elif declines >= 2:
            result["result"] = "ineffective"
            result["note"] = "优化后表现下降"
        else:
            result["result"] = "inconclusive"
            result["note"] = "效果不明显或指标冲突"

        # 保存结果到优化记录
        opt.comparison_result = result["result"]
        opt.comparison_summary = result["note"]
        db.commit()

        return result

    def _get_metrics_since(self, db: Session, article_id: int, since: datetime) -> list:
        """获取文章从指定时间开始的指标（按日期倒序）"""
        if not since:
            since = datetime.utcnow() - timedelta(days=30)
        return (
            db.query(ArticleMetrics)
            .filter(
                ArticleMetrics.article_id == article_id,
                ArticleMetrics.created_at >= since,
                ArticleMetrics.sync_status == "success",
            )
            .order_by(ArticleMetrics.metric_date.desc())
            .all()
        )


comparison_service = OptimizationComparisonService()
