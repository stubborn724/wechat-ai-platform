"""ERP 产品展示名补全服务。

ERP 接口经常只返回产品编号。本文服务在不修改原始编号的前提下，借助一次产品主图
视觉分析补充保守的中文品类说明，形成可供标题、正文和图片 Agent 共用的展示名。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings


logger = logging.getLogger(__name__)
_CHINESE_CHARACTER_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_DESCRIPTION_CHARACTER_PATTERN = re.compile(r"[^\u4e00-\u9fff、，\s]")
_DESCRIPTION_MAX_LENGTH = 16
_FALLBACK_CATEGORY_DESCRIPTION = "家具单品"


async def enrich_erp_product_display_name(
    *,
    product_name: str,
    image_url: str,
    analyze_image: Callable[[str], Awaitable[str]] | None = None,
) -> str:
    """为纯编号 ERP 产品补充中文品类说明，并保留原始编号。

    ERP 已返回中文名称时直接采用，避免无意义调用视觉模型和覆盖人工维护名称。
    只有纯编号或英文编号才分析主图；模型异常、空结果或不可信结果均回退通用品类，
    保证命名增强不是文章生成与发布的单点故障。
    """
    normalized_product_name = str(product_name or "").strip() or "未命名产品"
    if _CHINESE_CHARACTER_PATTERN.search(normalized_product_name):
        return normalized_product_name

    analyzer = analyze_image or _analyze_product_category_with_visual_agent
    try:
        category_description = _normalize_category_description(await analyzer(image_url))
    except Exception as exc:
        logger.warning("ERP 产品中文说明识别失败 product=%s: %s", normalized_product_name, exc)
        category_description = ""

    return f"{normalized_product_name} {category_description or _FALLBACK_CATEGORY_DESCRIPTION}"


async def _analyze_product_category_with_visual_agent(image_url: str) -> str:
    """调用视觉 Agent，为产品主图生成严格受限的中文品类候选。

    通用视觉理解 Agent 的 ``subject`` 通常包含场景和材质长句，不适合作为产品名。
    此处沿用项目已有的视觉模型与鉴权配置，但以专用命名提示词限制输出；每篇任务
    只针对唯一 ERP 主图调用一次，不会对后续 4 到 5 张背景图重复分析。
    """
    prompt = (
        "你是家具产品图片分析员。请只根据图片中最主要的家具，输出一个准确、保守的中文品类名称。\n"
        "要求：只输出 4 到 10 个中文字符；不要句子、标点、材质、颜色、尺寸、品牌、系列、型号或营销词；"
        "看不清时输出“家具单品”。示例：双层圆形边几、异形子母茶几、软包休闲椅。"
    )
    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-vl-max",
        temperature=0,
        max_tokens=32,
    )
    response = await llm.ainvoke([
        HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]),
    ])
    content = response.content
    if isinstance(content, list):
        content = "".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
        )
    return str(content or "").strip()


def _normalize_category_description(candidate: str) -> str:
    """将视觉主体描述收敛为可展示、不过度承诺的中文品类短语。

    视觉 Agent 的主体描述可能带有场景、材质猜测、标点或型号。这里只保留中文、
    顿号和逗号，并限制长度；不把尺寸、价格、系列等无法由单张图片确认的内容
    传给文章 Agent，从而降低幻觉对产品文案的影响。
    """
    cleaned = _DESCRIPTION_CHARACTER_PATTERN.sub("", str(candidate or ""))
    cleaned = re.sub(r"[、，\s]+", "", cleaned)
    if not _CHINESE_CHARACTER_PATTERN.search(cleaned):
        return ""
    return cleaned[:_DESCRIPTION_MAX_LENGTH]
