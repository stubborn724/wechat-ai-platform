"""ERP 分类配图定时任务的核心策略测试。"""

from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """策略测试不访问数据库，覆盖全局数据库夹具。"""
    yield


def test_due_slots_allow_each_configured_time_once_per_day():
    """同一任务在一天内应分别触发四个时间点，不能被首次执行锁死。"""
    from app.services.scheduled_erp_image_policy import find_due_schedule_times

    due = find_due_schedule_times(
        publish_times=["08:00", "12:00", "15:00", "17:00"],
        now=datetime(2026, 7, 28, 12, 2),
        completed_schedule_times={"08:00"},
        grace_minutes=5,
    )

    assert due == ["12:00"]


def test_due_slots_do_not_replay_old_times_after_grace_window():
    """服务恢复时不能把当天所有错过时段一次性补发。"""
    from app.services.scheduled_erp_image_policy import find_due_schedule_times

    due = find_due_schedule_times(
        publish_times=["08:00", "12:00", "15:00", "17:00"],
        now=datetime(2026, 7, 28, 17, 6),
        completed_schedule_times=set(),
        grace_minutes=5,
    )

    assert due == []


def test_select_products_excludes_images_used_in_recent_three_days():
    """ERP 分类选图必须排除窗口期内已使用的远端图片，再随机选择剩余图片。"""
    from app.services.erp_product_service import ErpProduct
    from app.services.scheduled_erp_image_policy import select_unused_erp_products

    candidates = [
        ErpProduct(name="茶几 A", image_url="https://erp.example/a.jpg", series=[], style="", categories=["茶几"], tags=[]),
        ErpProduct(name="茶几 B", image_url="https://erp.example/b.jpg", series=[], style="", categories=["茶几"], tags=[]),
        ErpProduct(name="茶几 C", image_url="https://erp.example/c.jpg", series=[], style="", categories=["茶几"], tags=[]),
    ]

    selected = select_unused_erp_products(
        candidates=candidates,
        recent_image_urls={"https://erp.example/a.jpg"},
        requested_count=2,
        shuffle=lambda products: products.reverse(),
    )

    assert [product.image_url for product in selected] == [
        "https://erp.example/c.jpg",
        "https://erp.example/b.jpg",
    ]


def test_select_products_fails_when_category_cannot_satisfy_no_repeat_window():
    """素材不足时必须显式失败，不能复用近三天图片伪装成功。"""
    from app.services.erp_product_service import ErpProduct
    from app.services.scheduled_erp_image_policy import ErpImageSelectionError, select_unused_erp_products

    candidates = [
        ErpProduct(name="茶几 A", image_url="https://erp.example/a.jpg", series=[], style="", categories=["茶几"], tags=[]),
    ]

    with pytest.raises(ErpImageSelectionError, match="三天内未重复"):
        select_unused_erp_products(
            candidates=candidates,
            recent_image_urls={"https://erp.example/a.jpg"},
            requested_count=1,
        )


def test_scheduled_erp_config_allows_entire_source_without_category():
    """四个 ERP 来源轮换时可从各来源全部产品中选图，不应强制填写分类。"""
    from app.services.scheduled_erp_image_service import parse_scheduled_erp_image_config

    config = parse_scheduled_erp_image_config({
        "source_key": "xiuman",
        "repeat_after_days": 3,
        "image_count": 8,
    })

    assert config is not None
    assert config.source_key == "xiuman"
    assert config.commodity_category is None


@pytest.mark.asyncio
async def test_prepared_erp_image_contains_cos_reference_url(monkeypatch):
    """归档后的 ERP 图片必须经 COS 中转，万相不能继续读取本地 MinIO 地址。"""
    from app.services.erp_product_service import ErpProduct
    from app.services import scheduled_erp_image_service as service_module

    product = ErpProduct(
        name="云朵茶几",
        image_url="https://erp.example/table.jpg",
        series=[],
        style="现代",
        categories=["茶几"],
        tags=[],
    )
    archived_asset = SimpleNamespace(
        id=31,
        storage_key="assets/auto/107/table.jpg",
        mime_type="image/jpeg",
    )

    async def fake_load_products(config, recent_urls, requested_count):
        """隔离 ERP 网络，仅返回当前用例所需的一件产品。"""
        return [product]

    async def fake_import_product_image(**kwargs):
        """模拟图片已完成本地归档，供中转逻辑读取其存储键。"""
        return archived_asset, "http://localhost:9002/wechat-assets/assets/auto/107/table.jpg"

    class FakeDb:
        """只记录新增实体和 flush，避免策略测试依赖真实数据库。"""

        def __init__(self):
            self.added = []
            self.flushed = False

        def add(self, entity):
            self.added.append(entity)

        def flush(self):
            self.flushed = True

    class FakeRelay:
        """记录上传字节并返回固定 COS 引用，验证依赖方向。"""

        def __init__(self):
            self.stage_calls = []

        def stage_bytes(self, **kwargs):
            self.stage_calls.append(kwargs)
            return SimpleNamespace(
                object_key="temporary/107/6/table.jpg",
                signed_url="https://cos.example.com/signed-image",
            )

    fake_db = FakeDb()
    fake_relay = FakeRelay()
    monkeypatch.setattr(service_module, "_recent_erp_image_urls", lambda *args: set())
    monkeypatch.setattr(service_module, "_load_category_products", fake_load_products)
    monkeypatch.setattr(service_module, "_import_product_image", fake_import_product_image)
    monkeypatch.setattr(
        service_module.storage_service,
        "download_bytes",
        lambda storage_key: b"archived-image-bytes",
    )
    monkeypatch.setattr(
        service_module,
        "normalize_reference_image",
        lambda data, content_type: SimpleNamespace(
            data=b"wanxiang-compatible-image-bytes",
            content_type="image/jpeg",
        ),
        raising=False,
    )

    prepared = await service_module.prepare_erp_images_for_scheduled_run(
        db=fake_db,
        task_id=4,
        tenant_id=107,
        run_id=6,
        config=service_module.ScheduledErpImageConfig(source_key="xiuman", image_count=1),
        requested_count=1,
        relay_service=fake_relay,
    )

    assert prepared[0].local_url.startswith("http://localhost:9002/")
    assert prepared[0].reference_url == "https://cos.example.com/signed-image"
    assert prepared[0].relay_object_key == "temporary/107/6/table.jpg"
    assert prepared[0].reference_image_bytes == b"wanxiang-compatible-image-bytes"
    assert prepared[0].reference_content_type == "image/jpeg"
    assert fake_relay.stage_calls == [{
        "data": b"wanxiang-compatible-image-bytes",
        "content_type": "image/jpeg",
        "tenant_id": 107,
        "run_id": 6,
    }]
    assert fake_db.flushed is True
