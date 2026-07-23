"""FastAPI 应用入口"""

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import mysql_engine, pg_engine

logger = logging.getLogger(__name__)

# ── Background worker: 不需要 Celery 也能跑定时任务 ────────────
_running = True


def _bg_worker():
    """后台工作线程：轮询定时任务 + 消费内容任务"""
    from app.tasks.scheduled_task_executor import check_scheduled_tasks
    from app.database import MysqlSessionLocal
    from app.models.mysql_models import ContentJob
    from app.services.job_queue_service import create_slot_articles, transition_job

    check_interval = 60 * 5  # 每 5 分钟检查定时任务
    poll_interval = 30       # 每 30 秒检查待处理任务
    last_check = 0
    last_poll = 0

    while _running:
        now = time.time()

        # 1. 检查定时任务（每 5 分钟）
        if now - last_check >= check_interval:
            try:
                check_scheduled_tasks()
            except Exception as e:
                logger.error("bg_worker check_scheduled_tasks error: %s", e)
            last_check = now

        # 2. 消费 queued 任务（每 30 秒）
        if now - last_poll >= poll_interval:
            try:
                db = MysqlSessionLocal()
                try:
                    jobs = (
                        db.query(ContentJob)
                        .filter(ContentJob.status == "queued")
                        .order_by(ContentJob.created_at.asc())
                        .limit(3)
                        .all()
                    )
                    for job in jobs:
                        logger.info("bg_worker dispatching job %d", job.id)
                        from app.tasks.job_tasks import process_content_job
                        process_content_job(job.id)
                finally:
                    db.close()
            except Exception as e:
                logger.error("bg_worker poll error: %s", e)
            last_poll = now

        time.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup: start background worker
    t = threading.Thread(target=_bg_worker, daemon=True)
    t.start()
    logger.info("Background worker started")
    yield
    # shutdown
    _running = False
    mysql_engine.dispose()
    pg_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="WeChat AI Platform",
        description="微信公众号 AI 运营平台 - 统一 API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from app.api.v1 import (
        accounts,
        articles,
        assets,
        auth,
        content_jobs,
        feed_sources,
        health,
        imitation,
        knowledge_bases,
        publish_plans,
        reviews,
        scheduled_tasks,
        statistics,
        watermark_config,
        wechat_articles,
        wechat_interact,
    )
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
    app.include_router(accounts.router, prefix="/api/v1", tags=["accounts"])
    app.include_router(articles.router, prefix="/api/v1", tags=["articles"])
    app.include_router(content_jobs.router, prefix="/api/v1", tags=["content-jobs"])
    app.include_router(reviews.router, prefix="/api/v1", tags=["reviews"])
    app.include_router(assets.router, prefix="/api/v1", tags=["assets"])
    app.include_router(feed_sources.router, prefix="/api/v1", tags=["feed-sources"])
    app.include_router(knowledge_bases.router, prefix="/api/v1", tags=["knowledge-bases"])
    app.include_router(publish_plans.router, prefix="/api/v1", tags=["publish-plans"])
    app.include_router(scheduled_tasks.router, prefix="/api/v1", tags=["scheduled-tasks"])
    app.include_router(statistics.router, prefix="/api/v1", tags=["statistics"])
    app.include_router(imitation.router, prefix="/api/v1", tags=["imitation"])
    app.include_router(watermark_config.router, prefix="/api/v1", tags=["watermark-config"])
    app.include_router(wechat_interact.router, prefix="/api/v1", tags=["wechat-interact"])
    app.include_router(wechat_articles.router, prefix="/api/v1", tags=["wechat-articles"])

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        timeout_keep_alive=600,
    )
