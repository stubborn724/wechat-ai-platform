"""本地 API 后台线程的 TaGeAI 回调补偿测试。

本地桌面联调只启动 FastAPI 时，不会同时启动 Celery Beat。该测试锁定后台线程必须
复用持久化 outbox 的“扫描后投递”顺序，确保文章完成后 Gateway 能收到包含预览的终态。
"""


def test_background_callback_sync_scans_then_delivers_outbox(monkeypatch):
    """本地补偿必须先生成当前快照，再投递到期事件。"""

    from app import main
    from app.integrations.tageai import callback_delivery, service

    calls = []

    monkeypatch.setattr(service, "enqueue_current_callback_snapshots", lambda: calls.append("scan") or 2)
    monkeypatch.setattr(
        callback_delivery,
        "deliver_due_callback_events",
        lambda: calls.append("deliver") or {"delivered": 2, "failed": 0, "selected": 2},
    )

    result = main._deliver_tageai_callback_snapshots()

    assert calls == ["scan", "deliver"]
    assert result == {"created": 2, "delivered": 2, "failed": 0}
