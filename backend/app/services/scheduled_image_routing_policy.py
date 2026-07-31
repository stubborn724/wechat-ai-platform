"""定时图文任务的图片来源路由策略。

投喂源文章、ERP 产品和知识库不是互相替代的来源：投喂源决定文章结构与文字
风格，ERP 决定图像主体，知识库决定产品场景与背景。该模块只负责判断三者在
视觉链路中的职责，保持为纯函数，避免执行器因分散的布尔条件误用投喂源图片。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ScheduledImageRouteMode = Literal[
    "erp_knowledge_background",
    "reference_visual_imitation",
    "standard_generation",
]


@dataclass(frozen=True)
class ScheduledImageRoute:
    """一次定时任务的图片生成职责划分。

    ``load_reference_visuals`` 只控制是否提取并理解投喂源图片；它不影响投喂源
    HTML、正文与风格档案的加载，因此 ERP 任务仍能完整仿写文章结构。
    ``requires_knowledge_background`` 让调用方在选择 ERP 产品而未配置背景规则时
    尽早失败，避免生成没有品牌场景约束的产品图。
    """

    mode: ScheduledImageRouteMode
    load_reference_visuals: bool
    requires_knowledge_background: bool


def resolve_scheduled_image_route(
    *,
    has_erp_product: bool,
    has_feed_source: bool,
    has_knowledge_base: bool,
) -> ScheduledImageRoute:
    """依据显式来源决定图片生成模式。

    ERP 产品优先于投喂源视觉参考，这是业务上的硬约束：产品原图必须保留真实
    主体，知识库的完整规则用于生成不同背景。投喂源在该模式下仍被文章 Agent
    用于文案和结构，但其图片不会被提取、理解或传给图片生成模型。

    Args:
        has_erp_product: 任务是否配置 ERP 自动选产品规则。
        has_feed_source: 任务是否绑定投喂源，用于文章结构或风格仿写。
        has_knowledge_base: 任务是否绑定知识库背景与品牌规则。

    Returns:
        描述调用方应采用的图片路由以及必要配置约束的不可变策略对象。
    """

    # ERP 模式不因投喂源存在而退化为图片仿写。知识库缺失由调用方基于
    # requires_knowledge_background 给出准确错误，策略本身不混入持久化校验。
    if has_erp_product:
        return ScheduledImageRoute(
            mode="erp_knowledge_background",
            load_reference_visuals=False,
            requires_knowledge_background=True,
        )

    # 只有未配置 ERP 主体时，投喂源图片才有资格参与视觉理解和图像仿写。
    if has_feed_source:
        return ScheduledImageRoute(
            mode="reference_visual_imitation",
            load_reference_visuals=True,
            requires_knowledge_background=False,
        )

    # 旧的自由写作与纯知识库文章继续走普通图片需求分析，不改变历史行为。
    return ScheduledImageRoute(
        mode="standard_generation",
        load_reference_visuals=False,
        requires_knowledge_background=False,
    )
