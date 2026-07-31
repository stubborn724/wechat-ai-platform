import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """该文件只验证网关策略，不需要数据库清理。"""
    yield


def test_direct_wechat_api_is_blocked_when_relay_channel_is_enabled(monkeypatch):
    from app.config import settings
    from app.services.wechat_gateway_policy import ensure_direct_wechat_api_allowed

    monkeypatch.setattr(settings, "wechat_api_channel", "relay")

    with pytest.raises(RuntimeError, match="中转站模式"):
        ensure_direct_wechat_api_allowed("评论同步")


def test_direct_wechat_api_is_allowed_in_direct_channel(monkeypatch):
    from app.config import settings
    from app.services.wechat_gateway_policy import ensure_direct_wechat_api_allowed

    monkeypatch.setattr(settings, "wechat_api_channel", "direct")

    assert ensure_direct_wechat_api_allowed("评论同步") is None
