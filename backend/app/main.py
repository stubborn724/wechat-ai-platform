"""FastAPI 应用入口"""

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import mysql_engine, pg_engine

logger = logging.getLogger(__name__)

# ── 安全响应头中间件 ────────────


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应添加安全头"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com unpkg.com; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com fonts.googleapis.com; "
            "img-src 'self' data: https: http://*.qpic.cn https://*.wx.qlogo.cn https://picsum.photos; "
            "connect-src 'self' https://api.weixin.qq.com https://dashscope.aliyuncs.com; "
            "font-src 'self' data: fonts.gstatic.com; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        )
        return response


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
                        ct = job.content_type or "article"
                        if ct in ("image", "pure_image", "video"):
                            # 图片/视频需要 ffmpeg 等 Docker 依赖，由 Celery worker 处理
                            logger.info("bg_worker skipping %s job %d (handled by Celery)", ct, job.id)
                            continue
                        logger.info("bg_worker processing article job %d", job.id)
                        from app.tasks.job_tasks import process_content_job
                        process_content_job(job.id)
                finally:
                    db.close()
            except Exception as e:
                logger.error("bg_worker poll error: %s", e)
            last_poll = now

        time.sleep(15)


def _verify_security_config():
    """检查密钥和密码配置
    - 生产环境: fail-fast 拒绝启动
    - 开发环境: logger.warning 提醒
    """
    errors = []
    if settings.jwt_secret_key == "change-this-to-a-random-secret-key":
        errors.append("JWT_SECRET_KEY 使用了默认值，请在 .env 中设置为随机密钥")
    if settings.credential_key == "change-this-to-a-32-char-key!!":
        errors.append("CREDENTIAL_KEY 使用了默认值，请在 .env 中设置为 32 字符随机密钥")
    if settings.mysql_password in ("root123", ""):
        errors.append("MYSQL_PASSWORD 使用了弱密码或空密码，请修改")
    if settings.dashscope_api_key == "":
        errors.append("DASHSCOPE_API_KEY 未配置")
    if not errors:
        return

    if settings.environment == "production":
        msg = "\n".join(errors)
        logger.error("生产环境配置检查失败:\n%s", msg)
        raise RuntimeError(f"生产环境配置检查不通过:\n{msg}")
    else:
        for err in errors:
            logger.warning("安全配置警告: %s", err)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup: verify security config
    _verify_security_config()
    # start background worker
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

    # Security headers (CSP, XSS protection, etc.)
    app.add_middleware(SecurityHeadersMiddleware)

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
        content_assets,
        content_jobs,
        feed_sources,
        health,
        imitation,
        knowledge_bases,
        optimizations,
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
    app.include_router(optimizations.router, prefix="/api/v1", tags=["optimizations"])
    app.include_router(watermark_config.router, prefix="/api/v1", tags=["watermark-config"])
    app.include_router(content_assets.router, prefix="/api/v1", tags=["content-assets"])
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
