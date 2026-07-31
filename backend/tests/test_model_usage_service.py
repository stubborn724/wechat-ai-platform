"""统一模型用量账本测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """该文件只验证内存账本，不连接业务数据库。"""
    yield


def test_image_usage_counts_successful_images_by_provider_model_size_and_operation():
    """图片调用只按真实成功张数记录，不应换算成不存在的 token。"""
    from app.services.model_usage_service import (
        begin_model_usage_collection,
        end_model_usage_collection,
        record_image_generation_usage,
    )

    token = begin_model_usage_collection("image-usage-test")
    try:
        record_image_generation_usage(
            "kuai_openai_compatible",
            "gpt-image-2",
            "1024*1365",
            has_reference_image=True,
        )
        record_image_generation_usage(
            "kuai_openai_compatible",
            "gpt-image-2",
            "1024*1365",
            has_reference_image=True,
        )
    finally:
        summary = end_model_usage_collection(token)

    assert summary.total_tokens == 0
    assert summary.image_request_count == 2
    assert summary.image_breakdown == (
        "kuai_openai_compatible/gpt-image-2/1024*1365/image_to_image x2",
    )
