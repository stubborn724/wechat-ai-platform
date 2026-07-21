"""健康检查"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_mysql_db, get_pg_db

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "wechat-ai-platform"}


@router.get("/health/db")
async def db_health(
    mysql_db: Session = Depends(get_mysql_db),
    pg_db: Session = Depends(get_pg_db),
):
    results = {"mysql": False, "postgres": False}

    try:
        mysql_db.execute(text("SELECT 1"))
        results["mysql"] = True
    except Exception:
        pass

    try:
        pg_db.execute(text("SELECT 1"))
        results["postgres"] = True
    except Exception:
        pass

    return {"status": "ok" if all(results.values()) else "degraded", **results}
