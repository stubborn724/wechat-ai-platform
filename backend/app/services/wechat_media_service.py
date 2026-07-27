"""微信素材管理 — 本地资产 ↔ 微信 media_id 映射"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import Asset, WechatMediaAsset

logger = logging.getLogger(__name__)


async def get_or_prepare_image_media(
    db: Session,
    tenant_id: int,
    account_id: int,
    asset_id: int,
    force_refresh: bool = False,
) -> WechatMediaAsset:
    """获取或准备图片素材的 media_id

    检查本地 media 记录 → ready 且未过期 → 直接返回
    缺失/过期/失败 → 上传微信 → 保存结果
    """
    ma = db.query(WechatMediaAsset).filter(
        WechatMediaAsset.tenant_id == tenant_id,
        WechatMediaAsset.account_id == account_id,
        WechatMediaAsset.asset_id == asset_id,
        WechatMediaAsset.media_type == "image",
    ).first()

    if not force_refresh and ma and ma.status == "ready" and ma.media_id:
        if ma.expires_at is None:
            return ma
        _now = datetime.now(timezone.utc)
        _exp = ma.expires_at
        if _exp.tzinfo is None:
            _exp = _exp.replace(tzinfo=timezone.utc)
        if _exp > _now:
            return ma

    # 新建或更新
    if not ma:
        ma = WechatMediaAsset(
            tenant_id=tenant_id,
            account_id=account_id,
            asset_id=asset_id,
            media_type="image",
            media_scope="temporary",
            status="pending",
        )
        db.add(ma)
        db.commit()
        db.refresh(ma)

    # mock 模式
    if settings.wechat_send_mode == "mock":
        ma.status = "ready"
        ma.media_id = f"mock_{uuid.uuid4().hex[:16]}"
        ma.uploaded_at = datetime.now(timezone.utc)
        ma.expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        ma.is_mock = True
        db.commit()
        db.refresh(ma)
        logger.info("Mock: prepared media_id %s for asset %d", ma.media_id, asset_id)
        return ma

    # 验证本地资产
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        ma.status = "failed"
        ma.last_error_message = "本地资产不存在"
        db.commit()
        raise ValueError(f"Asset {asset_id} not found")

    # live 模式 — 上传微信
    import httpx
    from app.services.wechat_comment_service import _get_service as _get_comment_svc

    ma.status = "uploading"
    db.commit()

    try:
        from app.models.mysql_models import WeChatAccount
        account = db.query(WeChatAccount).filter(
            WeChatAccount.id == account_id,
            WeChatAccount.deleted_at.is_(None),
        ).first()
        if not account:
            raise RuntimeError(f"Account {account_id} not found")

        # 复用 token 获取
        svc = await _get_comment_svc(db, account_id)
        token = svc.access_token

        # 下载本地文件
        import httpx as http2
        file_resp = await http2.AsyncClient(timeout=30.0).get(str(asset.url or asset.storage_key))
        file_resp.raise_for_status()

        # 上传到微信素材（临时素材接口 /cgi-bin/media/upload，有效期3天）
        async with http2.AsyncClient(timeout=30.0) as client:
            files = {"media": (asset.original_filename or "qr.png", file_resp.content, "image/png")}
            upload_resp = await client.post(
                "https://api.weixin.qq.com/cgi-bin/media/upload",
                params={"access_token": token, "type": "image"},
                files=files,
            )
            data = upload_resp.json()

        if data.get("errcode", 0) != 0:
            errcode = data.get("errcode", -1)
            errmsg = data.get("errmsg", "unknown")
            ma.status = "failed"
            ma.last_error_code = str(errcode)
            ma.last_error_message = errmsg
            ma.response_snapshot = data
            db.commit()
            raise RuntimeError(f"WeChat upload error {errcode}: {errmsg}")

        ma.status = "ready"
        ma.media_id = data.get("media_id", "")
        ma.media_scope = "temporary"
        ma.uploaded_at = datetime.now(timezone.utc)
        ma.expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        ma.last_error_code = None
        ma.last_error_message = None
        db.commit()
        db.refresh(ma)
        return ma

    except RuntimeError:
        raise
    except Exception as e:
        ma.status = "failed"
        ma.last_error_message = str(e)
        db.commit()
        raise


def get_media_asset(db: Session, tenant_id: int, media_id: int) -> Optional[dict]:
    """查询素材记录"""
    ma = db.query(WechatMediaAsset).filter(
        WechatMediaAsset.id == media_id,
        WechatMediaAsset.tenant_id == tenant_id,
    ).first()
    if not ma:
        return None
    return {
        "id": ma.id,
        "account_id": ma.account_id,
        "asset_id": ma.asset_id,
        "media_type": ma.media_type,
        "media_scope": ma.media_scope,
        "media_id": ma.media_id,
        "status": ma.status,
        "is_mock": ma.is_mock,
        "uploaded_at": ma.uploaded_at.isoformat() if ma.uploaded_at else None,
        "expires_at": ma.expires_at.isoformat() if ma.expires_at else None,
        "last_error_code": ma.last_error_code,
        "last_error_message": ma.last_error_message,
        "created_at": ma.created_at.isoformat() if ma.created_at else None,
    }


async def refresh_media(db: Session, tenant_id: int, media_id: int) -> Optional[dict]:
    """刷新素材（重新上传）"""
    ma = db.query(WechatMediaAsset).filter(
        WechatMediaAsset.id == media_id,
        WechatMediaAsset.tenant_id == tenant_id,
    ).first()
    if not ma:
        return None
    result = await get_or_prepare_image_media(
        db, tenant_id, ma.account_id, ma.asset_id, force_refresh=True
    )
    return {
        "id": result.id,
        "media_id": result.media_id,
        "status": result.status,
        "is_mock": result.is_mock,
    }
