"""微信 API 网关策略。

本模块集中管理“是否允许本机后端直连微信官方 API”的决策。把这个判断
独立出来，是为了避免发布、评论、客服消息、统计等模块各自写判断，导致
未来某个新功能又绕过中转站，重新把普通用户暴露到 IP 白名单问题里。
"""

from app.config import settings


def is_wechat_relay_enabled() -> bool:
    """判断当前是否启用固定 IP 中转站通道。"""
    return (settings.wechat_api_channel or "").strip().lower() == "relay"


def ensure_direct_wechat_api_allowed(capability: str) -> None:
    """在即将直连微信官方 API 前执行保护。

    Args:
        capability: 当前能力名称，例如“评论同步”“客服消息发送”。

    Raises:
        RuntimeError: relay 模式下禁止本机后端直连微信官方 API。
    """
    if not is_wechat_relay_enabled():
        return
    raise RuntimeError(
        f"当前启用微信中转站模式，禁止本机直连微信官方 API。"
        f"“{capability}”需要中转站提供对应接口后再启用。"
    )


def require_relay_publish_config() -> None:
    """校验文章发布中转站配置完整性。"""
    missing = []
    if not settings.wechat_relay_base_url:
        missing.append("WECHAT_RELAY_BASE_URL")
    if not settings.wechat_relay_app_id:
        missing.append("WECHAT_RELAY_APP_ID")
    if not settings.wechat_relay_secret:
        missing.append("WECHAT_RELAY_SECRET")
    if missing:
        raise RuntimeError(f"微信中转站配置缺失：{', '.join(missing)}")
