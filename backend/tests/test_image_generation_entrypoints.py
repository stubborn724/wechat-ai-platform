"""AI 图片生成入口依赖收敛测试。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """静态依赖测试不访问业务数据库。"""
    yield


def test_business_entrypoints_do_not_import_wanxiang_service_directly():
    """业务入口只能依赖统一图片服务，万相旧服务仅允许适配器访问。"""
    app_root = Path(__file__).resolve().parents[1] / "app"
    allowed_files = {
        app_root / "services" / "wanxiang_service.py",
        app_root / "services" / "wanxiang_image_provider.py",
    }
    forbidden_import = "app.services.wanxiang_service import WanxiangImageService"
    offenders = []
    for path in app_root.rglob("*.py"):
        if path in allowed_files:
            continue
        if forbidden_import in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(app_root).as_posix())

    assert offenders == []
