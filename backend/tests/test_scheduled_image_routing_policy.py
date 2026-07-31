"""定时文章图片来源优先级的回归测试。

投喂源、ERP 产品和知识库分别负责不同维度。该测试锁定 ERP 产品优先级，
避免后续在加载投喂源文章时又把其图片误送入视觉仿写链路。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块仅验证纯策略，禁止连接业务数据库。"""

    yield


def test_erp_product_with_feed_structure_uses_knowledge_background_only() -> None:
    """ERP 产品图应覆盖投喂源图片，知识库只负责生成背景规则。"""

    from app.services.scheduled_image_routing_policy import resolve_scheduled_image_route

    decision = resolve_scheduled_image_route(
        has_erp_product=True,
        has_feed_source=True,
        has_knowledge_base=True,
    )

    assert decision.mode == "erp_knowledge_background"
    assert decision.load_reference_visuals is False
    assert decision.requires_knowledge_background is True


def test_feed_source_without_erp_can_use_reference_visual_imitation() -> None:
    """仅在未选择 ERP 产品时，投喂源图片才允许作为视觉仿写参考。"""

    from app.services.scheduled_image_routing_policy import resolve_scheduled_image_route

    decision = resolve_scheduled_image_route(
        has_erp_product=False,
        has_feed_source=True,
        has_knowledge_base=False,
    )

    assert decision.mode == "reference_visual_imitation"
    assert decision.load_reference_visuals is True
    assert decision.requires_knowledge_background is False


def test_unconfigured_sources_use_standard_image_generation() -> None:
    """无 ERP 和投喂源时沿用普通文生图，不隐式要求知识库。"""

    from app.services.scheduled_image_routing_policy import resolve_scheduled_image_route

    decision = resolve_scheduled_image_route(
        has_erp_product=False,
        has_feed_source=False,
        has_knowledge_base=False,
    )

    assert decision.mode == "standard_generation"
    assert decision.load_reference_visuals is False
    assert decision.requires_knowledge_background is False
