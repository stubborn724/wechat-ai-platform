"""桥接模块：让 WeChatPublisher 可以使用 OAuth 授权 token 发布文章

替代原有的 AppID + AppSecret 方式，使用扫码授权后的 token 调用微信 API。
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.services.wechat_oauth_service import get_valid_token

logger = logging.getLogger(__name__)


async def get_account_token(db: Session, oauth_account_id: int) -> str:
    """获取 OAuth 授权公众号的有效 access_token"""
    return await get_valid_token(db, oauth_account_id)


def get_publisher_token_sync(db: Session, oauth_account_id: int) -> Optional[str]:
    """同步获取 OAuth token（供 WeChatPublisher 等同步代码使用）"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # 已在事件循环中，新建一个线程跑
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, get_valid_token(db, oauth_account_id))
            return future.result()
    else:
        return loop.run_until_complete(get_valid_token(db, oauth_account_id))
