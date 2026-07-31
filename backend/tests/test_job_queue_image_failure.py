"""任务队列图片生成失败兜底策略的回归测试。"""

import logging

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块只覆盖纯文本转换逻辑，不访问业务数据库。"""
    yield


def test_remove_unavailable_image_slots_keeps_article_text_without_random_image(caplog):
    """图片生成失败时必须移除图片槽位，不能注入随机图库地址。"""
    from app.services.job_queue_service import remove_unavailable_image_slots

    raw_content = (
        "# 家具选购指南\n\n"
        "[IMAGE:position=1,keywords=原木餐桌,type=inline]\n\n"
        "餐桌的材质决定了日常维护成本。\n\n"
        "[IMAGE:position=2,keywords=餐椅细节,type=inline]"
    )
    caplog.set_level(logging.ERROR, logger="app.services.job_queue_service")

    result = remove_unavailable_image_slots(raw_content, job_id=12, slot_index=3)

    assert "家具选购指南" in result
    assert "餐桌的材质决定了日常维护成本" in result
    assert "[IMAGE:" not in result
    assert "picsum.photos" not in result
    assert "任务图片生成失败，已移除未填充图片槽位" in caplog.text
    assert "job=12" in caplog.text
    assert "slot=3" in caplog.text


def test_legacy_marker_renderer_does_not_create_picsum_images(caplog):
    """旧文章接口没有生图结果时也必须删除槽位，不能回退为随机图库。"""
    from app.api.v1.articles import _render_image_markers

    caplog.set_level(logging.ERROR, logger="app.api.v1.articles")
    result = _render_image_markers(
        "正文内容\n\n[IMAGE:position=1,keywords=原木沙发,type=inline]",
        "task-123",
    )

    assert "正文内容" in result
    assert "[IMAGE:" not in result
    assert "picsum.photos" not in result
    assert "随机图库回退已阻止" in caplog.text
