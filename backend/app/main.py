"""FastAPI 应用入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import mysql_engine, pg_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup
    yield
    # shutdown
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
        statistics,
        wechat_oauth,
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
    app.include_router(statistics.router, prefix="/api/v1", tags=["statistics"])
    app.include_router(wechat_oauth.router, prefix="/api/v1", tags=["wechat-oauth"])
    app.include_router(imitation.router, prefix="/api/v1", tags=["imitation"])

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.server_host, port=settings.server_port, reload=True)
