"""初始化数据库表结构"""
# 导入模型类，让 SQLAlchemy 知道有哪些表要创建
import app.models.mysql_models  # noqa: F401
import app.models.pg_models  # noqa: F401

from app.database import mysql_engine, pg_engine, MysqlBase, PgBase

from sqlalchemy import text

print("正在创建 MySQL 表...")
MysqlBase.metadata.create_all(mysql_engine)
print("MySQL 表创建完成")

print("正在启用 pgvector 扩展...")
with pg_engine.connect() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.commit()
print("pgvector 扩展就绪")

print("正在创建 PostgreSQL 表...")
PgBase.metadata.create_all(pg_engine)
print("PostgreSQL 表创建完成")

print("所有表创建完毕！")
