"""微信公众号阅读指标服务 — 封装 datacube API"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.mysql_models import AccountCredential, WeChatAccount

logger = logging.getLogger(__name__)

_BASE = "https://api.weixin.qq.com"


class WeChatMetricsService:
    """微信文章数据统计接口封装

    对应接口: POST https://api.weixin.qq.com/datacube/getarticleint
    文档: https://developers.weixin.qq.com/doc/offiaccount/Analytics/Analytics_API.html
    """

    def __init__(self, access_token: str):
        self.token = access_token

    async def fetch_article_metrics(
        self, msg_data_id: str, start_date: str, end_date: str
    ) -> dict:
        """获取单篇文章的阅读指标

        Args:
            msg_data_id: 微信文章标识
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            { "list": [ { "ref_date": "...", "int_page_read_count": N, ... } ] }
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_BASE}/datacube/getarticleint",
                params={"access_token": self.token},
                json={
                    "begin_date": start_date,
                    "end_date": end_date,
                    "msg_data_id": msg_data_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(
                    f"WeChat API error: {data.get('errmsg', data)}"
                )
            return data

    def normalize_metrics(self, raw: dict, article_id: int, metric_date: str) -> dict:
        """标准化微信返回的指标数据"""
        items = raw.get("list", [])
        if not items:
            return {}

        # 取第一条（通常是汇总或当日数据）
        day_data = items[0]
        return {
            "article_id": article_id,
            "metric_date": metric_date,
            "read_count": day_data.get("int_page_read_count", 0),
            "like_count": day_data.get("share_user", 0),  # 微信接口中 like 可能用 share_user
            "share_count": day_data.get("share_count", 0),
            "comment_count": day_data.get("comment_count", 0),
            "add_to_fav_count": day_data.get("add_to_fav_count", 0),
            "exposure_count": day_data.get("int_page_read_user", None),
            "read_user_count": day_data.get("ori_page_read_user", None),
            "raw_payload": raw,
        }


async def get_metrics_service(
    db: Session, account_id: int, tenant_id: int = 0
) -> WeChatMetricsService:
    """构造 WeChatMetricsService 实例"""
    query = db.query(WeChatAccount).filter(
        WeChatAccount.id == account_id,
        WeChatAccount.deleted_at.is_(None),
    )
    if tenant_id:
        query = query.filter(WeChatAccount.tenant_id == tenant_id)
    account = query.first()
    if not account:
        raise ValueError(f"Account {account_id} not found")

    cred = db.query(AccountCredential).filter(
        AccountCredential.account_id == account_id,
    ).first()
    if not cred:
        raise ValueError(f"Credential for account {account_id} not found")

    # 获取 access_token
    from app.services.encryption_service import derive_key, decrypt_secret
    from app.config import settings

    key = derive_key(settings.credential_key)
    app_secret = decrypt_secret(cred.encrypted_secret, key)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_BASE}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": account.app_id,
                "secret": app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token", "")
    if not token:
        raise ValueError(f"Failed to get access_token: {data}")

    return WeChatMetricsService(token)
