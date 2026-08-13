"""公众号发布域策略。

本模块只处理“公域/私域”这一项跨层协议的值域和映射，不读取数据库，也不发起
网络请求。把纯函数集中在这里，可以让 API、定时任务和中转站客户端使用同一套
默认值与错误提示，避免某一层把私域悄悄当成公域。
"""

from __future__ import annotations

from typing import Literal


PUBLIC_PUBLISH_DOMAIN = "public"
PRIVATE_PUBLISH_DOMAIN = "private"
PublishDomain = Literal["public", "private"]


def normalize_publish_domain(value: str | None) -> PublishDomain:
    """规范化并校验发布域。

    历史任务没有该字段时回退到公域，这是兼容旧数据的关键约束；显式传入其他
    值则立即失败，避免发布服务在无法判断目标域时产生真实微信副作用。
    """

    normalized = str(value or PUBLIC_PUBLISH_DOMAIN).strip().lower()
    if normalized not in {PUBLIC_PUBLISH_DOMAIN, PRIVATE_PUBLISH_DOMAIN}:
        raise ValueError(f"不支持的发布域：{value}")
    return normalized  # type: ignore[return-value]


def map_relay_publish_mode(
    publish_mode: str,
    publish_domain: str | None,
    confirm_publish: bool,
) -> str:
    """把系统发布模式映射为中转站协议模式。

    存草稿不产生公域或私域副作用，因此无论域选择如何都使用
    ``draft_only``；直接发布则严格区分公域 ``public_publish`` 和私域
    ``follower_push``，并继续要求显式确认。
    """

    domain = normalize_publish_domain(publish_domain)
    if publish_mode == "draft":
        return "draft_only"
    if publish_mode == "direct":
        if not confirm_publish:
            raise ValueError("direct publish requires confirm_publish=True")
        return (
            "follower_push"
            if domain == PRIVATE_PUBLISH_DOMAIN
            else "public_publish"
        )
    raise ValueError(f"Unsupported publish mode: {publish_mode}")
