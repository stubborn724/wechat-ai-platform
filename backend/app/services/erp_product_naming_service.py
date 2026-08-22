"""ERP 产品展示名补全服务。

ERP 接口经常只返回产品编号。本文服务在不修改原始编号的前提下，借助一次产品主图
视觉分析补充保守的中文品类说明，形成可供标题、正文和图片 Agent 共用的展示名。
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image

from app.config import settings


logger = logging.getLogger(__name__)
_CHINESE_CHARACTER_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_DESCRIPTION_CHARACTER_PATTERN = re.compile(r"[^\u4e00-\u9fff、，\s]")
_DESCRIPTION_MAX_LENGTH = 16
_FALLBACK_CATEGORY_DESCRIPTION = "家具单品"
_UNRELIABLE_CATEGORY_DESCRIPTIONS = {
    _FALLBACK_CATEGORY_DESCRIPTION,
    "未识别家具",
    "未命名产品",
}
_PRODUCT_VISION_MAX_SIDE = 2048
_PRODUCT_VISION_JPEG_QUALITY = 82


@dataclass(frozen=True)
class NormalizedProductVisionImage:
    """发送给轻量视觉模型的受控图片输入。"""

    data: bytes
    content_type: str


@dataclass(frozen=True)
class ProductVisionProvider:
    """ERP 商品品类识别的单个视觉提供商配置。

    将调用地址、密钥和模型标识作为不可变对象传递，避免主备切换时混用 Kuai
    公共模型名与方舟 Endpoint ID。方舟 OpenAI 兼容接口只能接收接入点 ID，
    因此该边界能把容易误配的差异限制在配置解析层。
    """

    name: str
    base_url: str
    api_key: str
    model: str


async def enrich_erp_product_display_name(
    *,
    product_name: str,
    image_url: str,
    fallback_category: str | None = None,
    analyze_image: Callable[[str], Awaitable[str]] | None = None,
) -> str:
    """为纯编号 ERP 产品补充中文品类说明，并保留原始编号。

    ERP 已返回中文名称时直接采用，避免无意义调用视觉模型和覆盖人工维护名称。
    只有纯编号或英文编号才分析主图；模型异常、空结果或不可信结果优先回退 ERP
    已确认的分类品类，再回退通用品类。分类由调用方从 ERP 标签和分类确定，不增加
    模型请求，因此视觉额度耗尽时仍能保持标题、正文和图片对产品主体的语义一致。
    """
    normalized_product_name = str(product_name or "").strip() or "未命名产品"
    if _CHINESE_CHARACTER_PATTERN.search(normalized_product_name):
        return normalized_product_name

    deterministic_category = _normalize_category_description(fallback_category or "")
    if (
        deterministic_category
        and deterministic_category not in _UNRELIABLE_CATEGORY_DESCRIPTIONS
    ):
        # ERP 分类、标签或历史素材标签已经给出明确品类时，视觉模型不能再推翻
        # 这份确定性事实。直接返回既省去一次图片下载和模型调用，也避免额外延时。
        return f"{normalized_product_name} {deterministic_category}"

    analyzer = analyze_image or _analyze_product_category_with_visual_agent
    try:
        category_description = _normalize_category_description(await analyzer(image_url))
    except Exception as exc:
        logger.warning("ERP 产品中文说明识别失败 product=%s: %s", normalized_product_name, exc)
        category_description = ""

    resolved_category = (
        category_description
        or deterministic_category
        or _FALLBACK_CATEGORY_DESCRIPTION
    )
    return f"{normalized_product_name} {resolved_category}"


async def _analyze_product_category_with_visual_agent(image_url: str) -> str:
    """调用视觉 Agent，为产品主图生成严格受限的中文品类候选。

    通用视觉理解 Agent 的 ``subject`` 通常包含场景和材质长句，不适合作为产品名。
    此处优先使用项目现有 Kuai 网关中的 ``qwen3-vl-8b-instruct``。当中转站临时
    不可用时，再尝试方舟视觉 Endpoint。每篇任务只下载并压缩一次 ERP 主图，主备
    调用共享同一份 data URI，避免重复访问 ERP 造成额外网络失败。
    """
    prompt = (
        "你是家具产品图片分析员。请只根据图片中最主要的家具，输出一个准确、保守的中文品类名称。\n"
        "要求：只输出 4 到 10 个中文字符；不要句子、标点、材质、颜色、尺寸、品牌、系列、型号或营销词；"
        "看不清时输出“家具单品”。示例：双层圆形边几、异形子母茶几、软包休闲椅。"
    )
    normalized_image = await _download_and_normalize_product_vision_image(image_url)
    image_data_uri = (
        f"data:{normalized_image.content_type};base64,"
        f"{base64.b64encode(normalized_image.data).decode('ascii')}"
    )
    last_error: Exception | None = None
    for provider in _build_product_vision_provider_chain():
        try:
            return await _invoke_product_vision_provider(
                provider=provider,
                prompt=prompt,
                image_data_uri=image_data_uri,
            )
        except Exception as exc:
            last_error = exc
            # 这里不记录 URL 和密钥，既能保留供应商故障线索，也避免把 ERP 素材地址
            # 或敏感配置泄露到任务日志。所有备用都失败时由上层回退确定性分类。
            logger.warning(
                "ERP 产品视觉识别 provider=%s 调用失败，继续尝试下一层: %s",
                provider.name,
                exc,
            )
    raise RuntimeError("ERP 产品视觉识别所有提供商均不可用") from last_error


def _build_product_vision_provider_chain() -> tuple[ProductVisionProvider, ...]:
    """构造稳定的 Kuai 主用、方舟备用视觉识别链路。

    未完整配置方舟地址、密钥或 Endpoint ID 时不创建半成品备用层。这样新环境仍能
    保持原来的 Kuai 行为，而不会因空配置浪费一次失败调用或覆盖主服务结果。
    """
    providers = [
        ProductVisionProvider(
            name="kuai",
            base_url=settings.text_generation_base_url,
            api_key=settings.text_generation_api_key,
            model=settings.erp_product_vision_model,
        )
    ]
    ark_base_url = settings.erp_product_vision_ark_base_url.strip()
    ark_api_key = settings.erp_product_vision_ark_api_key.strip()
    ark_model = settings.erp_product_vision_ark_model.strip()
    if ark_base_url and ark_api_key and ark_model:
        providers.append(
            ProductVisionProvider(
                name="volcengine_ark",
                base_url=ark_base_url,
                api_key=ark_api_key,
                model=ark_model,
            )
        )
    return tuple(providers)


async def _invoke_product_vision_provider(
    *,
    provider: ProductVisionProvider,
    prompt: str,
    image_data_uri: str,
) -> str:
    """以统一 OpenAI 兼容协议调用一个视觉提供商并提取文本结果。"""
    llm = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=provider.model,
        temperature=0,
        max_tokens=96,
        timeout=settings.erp_product_vision_timeout_seconds,
    )
    response = await llm.ainvoke([
        HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
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


async def _download_and_normalize_product_vision_image(
    image_url: str,
) -> NormalizedProductVisionImage:
    """下载 ERP 中转图并压缩为轻量视觉模型稳定接受的 JPEG。"""

    normalized_url = str(image_url or "").strip()
    if not normalized_url:
        raise ValueError("ERP 产品视觉识别缺少图片地址")
    timeout = httpx.Timeout(settings.erp_product_vision_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(normalized_url)
        response.raise_for_status()
    return normalize_product_vision_image(
        response.content,
        response.headers.get("content-type") or "image/jpeg",
    )


def normalize_product_vision_image(
    data: bytes,
    content_type: str,
) -> NormalizedProductVisionImage:
    """压缩商品图，兼顾视觉分类细节与低成本模型的输入体积限制。"""

    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        raise ValueError("ERP 产品视觉识别图片不能为空")
    try:
        with Image.open(BytesIO(bytes(data))) as source:
            source.load()
            image = source.convert("RGB")
            image.thumbnail(
                (_PRODUCT_VISION_MAX_SIDE, _PRODUCT_VISION_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=_PRODUCT_VISION_JPEG_QUALITY,
                optimize=True,
            )
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("ERP 产品视觉识别图片无法解码") from exc
    return NormalizedProductVisionImage(
        data=output.getvalue(),
        content_type="image/jpeg",
    )


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
