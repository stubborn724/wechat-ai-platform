"""文章优化生成与效果评估 Celery 任务"""

import asyncio
import logging

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import Article, ArticleOptimization, ArticleQualityEvaluation
from app.services.article_optimization_service import optimization_service
from app.services.optimization_comparison_service import comparison_service

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def generate_optimization_draft(self, article_id: int, opt_type: str,
                                evaluation_id: int = 0, instruction: str = ""):
    """为文章生成优化草稿"""
    db = MysqlSessionLocal()
    try:
        article = db.query(Article).filter(Article.id == article_id).first()
        if not article:
            return {"error": "article_not_found"}

        async def _do_generate():
            return await optimization_service.generate(
                db, article, opt_type,
                instruction=instruction,
                evaluation_id=evaluation_id or None,
            )

        result = asyncio.run(_do_generate())
        logger.info("Generated optimization draft for article %d: type=%s, opt_id=%s",
                    article_id, opt_type, result.get("optimization_id"))
        return result

    except ValueError as exc:
        logger.warning("Cannot generate optimization for article %d: %s", article_id, exc)
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("Optimization generation failed for article %d: %s", article_id, exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task
def check_optimization_candidates():
    """Beat 任务：扫描需要优化的文章，自动生成优化草稿"""
    db = MysqlSessionLocal()
    try:
        candidates = (
            db.query(Article)
            .filter(
                Article.optimization_status == "suggested",
                Article.latest_quality_score.isnot(None),
                Article.latest_quality_score < 50,
                Article.manual_optimization_disabled == False,
            )
            .order_by(Article.latest_quality_score.asc())
            .limit(5)
            .all()
        )

        dispatched = 0
        for article in candidates:
            # 获取最近的评分记录
            evaluation = (
                db.query(ArticleQualityEvaluation)
                .filter(
                    ArticleQualityEvaluation.article_id == article.id,
                    ArticleQualityEvaluation.status == "success",
                )
                .order_by(ArticleQualityEvaluation.id.desc())
                .first()
            )

            opt_type = "full_rewrite"
            if article.latest_quality_score >= 40:
                opt_type = "structure_optimize"

            generate_optimization_draft.delay(
                article.id, opt_type,
                evaluation_id=evaluation.id if evaluation else 0,
            )
            article.optimization_status = "generating"
            dispatched += 1

        db.commit()
        logger.info("Dispatched %d optimization candidates", dispatched)
        return {"dispatched": dispatched}

    except Exception as exc:
        logger.error("check_optimization_candidates failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task
def evaluate_optimization_effect(optimization_id: int):
    """评估优化效果"""
    db = MysqlSessionLocal()
    try:
        result = comparison_service.compare(db, optimization_id)
        logger.info("Optimization effect for %d: %s", optimization_id, result.get("result"))
        return result
    except Exception as exc:
        logger.error("Effect evaluation failed for optimization %d: %s", optimization_id, exc)
        return {"error": str(exc)}
    finally:
        db.close()
