"""双数据源配置：MySQL (业务) + PostgreSQL/pgvector (向量)"""

from collections.abc import AsyncGenerator
from typing import AsyncGenerator as AsyncGen

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# ============================================================
# MySQL - 业务数据库
# ============================================================
mysql_engine = create_engine(
    settings.mysql_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

MysqlSessionLocal = sessionmaker(
    bind=mysql_engine,
    autocommit=False,
    autoflush=False,
)


class MysqlBase(DeclarativeBase):
    pass


def get_mysql_db():
    """同步 MySQL session 依赖注入"""
    db = MysqlSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# PostgreSQL - 向量数据库 (pgvector)
# ============================================================
try:
    pg_engine = create_engine(
        settings.pg_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
except Exception:
    pg_engine = None

PgSessionLocal = sessionmaker(
    bind=pg_engine,
    autocommit=False,
    autoflush=False,
)


class PgBase(DeclarativeBase):
    pass


def get_pg_db():
    """同步 PostgreSQL session 依赖注入"""
    db = PgSessionLocal()
    try:
        yield db
    finally:
        db.close()
