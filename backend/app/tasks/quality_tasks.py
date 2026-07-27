"""文章质量评分 Celery 任务"""

import asyncio
import logging
from datetime import datetime

from app.celery_app import celery_app
from app.database import MysqlSessionLocal
from app.models.mysql_models import Article, ArticleQualityEvaluation
from app.services.quality_service import quality_service

logger = logging.getLogger(__name__)

# 评分阈值配置
OPTIMIZATION_THRESHOLD = 50  # 总分低于此值进入优化候选
AUTO_OPTIMIZE_THRESHOLD = 40  # 总分低于此值自动推荐优化


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def evaluate_article_quality(self, article_id: int):
    """对单篇文章执行 AI 质量评分"""
    db = MysqlSessionLocal()
    try:
        article = db.query(Article).filter(Article.id == article_id).first()
        if not article:
            logger.warning("Article %d not found, skipping evaluation", article_id)
            return {"error": "article_not_found"}

        # 去重检查：相同内容哈希 + 相同模型版本 不重复评分
        from app.services.quality_service import ArticleQualityService
        checker = ArticleQualityService()
        content_hash = checker._compute_content_hash(article)

        existing = (
            db.query(ArticleQualityEvaluation)
            .filter(
                ArticleQualityEvaluation.article_id == article_id,
                ArticleQualityEvaluation.input_content_hash == content_hash,
                ArticleQualityEvaluation.status == "success",
            )
            .first()
        )
        if existing:
            logger.info("Article %d already evaluated (hash=%s), skipping", article_id, content_hash)
            return {"article_id": article_id, "status": "already_evaluated", "score": existing.overall_score}

        # 执行评分
        async def _do_eval():
            return await quality_service.evaluate(article)

        result = asyncio.run(_do_eval())

        # 保存评分记录
        eval_record = ArticleQualityEvaluation(
            article_id=article_id,
            content_score=result.get("content_score"),
            readability_score=result.get("readability_score"),
            structure_score=result.get("structure_score"),
            value_score=result.get("value_score"),
            title_score=result.get("title_score"),
            title_consistency_score=result.get("title_consistency_score"),
            credibility_score=result.get("credibility_score"),
            overall_score=result["overall_score"],
            issues=result.get("issues"),
            suggestions=result.get("suggestions"),
            rewrite_recommended=result.get("rewrite_recommended", False),
            rewrite_scope=result.get("rewrite_scope"),
            factual_risk=result.get("factual_risk"),
            brand_risk=result.get("brand_risk"),
            confidence=result.get("confidence"),
            model_name=result["model_name"],
            model_version=result.get("model_version"),
            prompt_version=result.get("prompt_version"),
            input_content_hash=content_hash,
            raw_response=result.get("raw_response"),
            status="success",
            evaluated_at=datetime.utcnow(),
        )
        db.add(eval_record)

        # 更新 Article 缓存
        article.latest_quality_score = result["overall_score"]
        article.quality_evaluated_at = datetime.utcnow()

        # 判断是否进入优化候选
        if result["overall_score"] < OPTIMIZATION_THRESHOLD:
            article.optimization_status = "suggested"
            logger.info("Article %d marked for optimization (score=%d)",
                       article_id, result["overall_score"])

        db.commit()
        logger.info("Evaluated article %d: overall_score=%d", article_id, result["overall_score"])
        return {"article_id": article_id, "score": result["overall_score"], "status": "success"}

    except Exception as exc:
        logger.error("Quality evaluation failed for article %d: %s", article_id, exc)
        # 保存失败记录
        try:
            fail_record = ArticleQualityEvaluation(
                article_id=article_id,
                status="failed",
                error_message=str(exc)[:500],
                model_name="qwen-plus",
                prompt_version="v1.0",
            )
            db.add(fail_record)
            db.commit()
        except Exception:
            db.rollback()
        return {"error": str(exc)}
    finally:
        db.close()


@celery_app.task
def batch_evaluate_articles():
    """Beat 任务：扫描未评分的已发布文章，批量评分（每批 10 篇）"""
    db = MysqlSessionLocal()
    try:
        articles = (
            db.query(Article)
            .filter(
                Article.status == "published",
                Article.latest_quality_score.is_(None),
            )
            .order_by(Article.id.desc())
            .limit(10)
            .all()
        )

        dispatched = 0
        for article in articles:
            evaluate_article_quality.delay(article.id)
            dispatched += 1

        logger.info("Batch evaluated %d articles", dispatched)
        return {"dispatched": dispatched}

    except Exception as exc:
        logger.error("batch_evaluate_articles failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()
