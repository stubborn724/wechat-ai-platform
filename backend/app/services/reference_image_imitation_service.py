"""参考图片仿写的共享编排服务。

图文和纯图片入口都需要使用视觉理解结果生成新图片。该模块只协调“参考图片到
生成图片”的流程：二维码过滤委托给 ``reference_media_analysis_service``，业务入口
仍负责查询参考文章、保存文章和发布。通过注入外部 Agent 与存储函数，可避免服务
绑定 HTTP、数据库或特定图片模型，并让核心规则能够独立测试。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.services.reference_media_analysis_service import analyze_reference_images


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferenceImageImitationResult:
    """参考图片仿写的结果摘要。

    ``generated_urls`` 只包含实际成功生成的图片，调用方据此决定是否创建纯图片
    文章。两个跳过计数用于日志和可观测性，避免二维码或无效视觉结果被悄悄忽略。
    """

    generated_urls: tuple[str, ...]
    skipped_qrcode_count: int
    skipped_invalid_count: int


async def imitate_reference_images(
    image_urls: Sequence[str],
    topic: str,
    *,
    tenant_id: int,
    understand_images_fn: Callable[[list[str]], list[dict]],
    craft_prompt_fn: Callable[..., dict],
    fallback_prompt_fn: Callable[[dict, str, str], str],
    generate_image_fn: Callable[..., Awaitable[str | None]],
    archive_image_fn: Callable[..., Awaitable[Any]],
) -> ReferenceImageImitationResult:
    """跳过二维码后，按参考顺序仿写其余图片。

    图片理解返回值可能缺项或格式异常，因此只处理分析服务确认可用的描述。每张图片
    的提示词、生成和归档错误彼此隔离：单张失败不能阻断后续普通图片，也不能让二
    维码退回到提示词构建流程。
    """
    analysis = analyze_reference_images(image_urls, understand_images_fn)
    valid_description_count = len(analysis.usable_images)
    skipped_invalid_count = max(
        len([url for url in image_urls if str(url or "").strip()])
        - valid_description_count
        - analysis.skipped_qrcode_count,
        0,
    )
    generated_urls: list[str] = []

    for reference_image in analysis.usable_images:
        prompt = build_reference_image_prompt(
            reference_image.description,
            topic,
            craft_prompt_fn,
            fallback_prompt_fn,
        )
        if not prompt:
            logger.warning("参考图片 %s 未生成可用提示词，已跳过", reference_image.source_index)
            continue

        try:
            image_url = await generate_image_fn(prompt, size="1024*1365")
        except Exception as exc:
            logger.warning("参考图片 %s 生成失败: %s", reference_image.source_index, exc)
            continue
        if not image_url:
            logger.warning("参考图片 %s 未返回生成地址", reference_image.source_index)
            continue

        generated_urls.append(image_url)
        try:
            await archive_image_fn(tenant_id, image_url, keywords=topic[:50])
        except Exception as exc:
            # 归档失败不影响已生成图片交付，入口仍可使用模型返回的公网地址。
            logger.warning("生成图片归档失败，继续使用原地址: %s", exc)

    return ReferenceImageImitationResult(
        generated_urls=tuple(generated_urls),
        skipped_qrcode_count=analysis.skipped_qrcode_count,
        skipped_invalid_count=skipped_invalid_count,
    )


def build_reference_image_prompt(
    description: dict,
    topic: str,
    craft_prompt_fn: Callable[..., dict],
    fallback_prompt_fn: Callable[[dict, str, str], str],
) -> str:
    """为单张已确认非二维码的参考图构建高相似度生成提示词。

    图文流程需要按正文占位符重复使用视觉描述，而纯图片流程只需每张参考图生成
    一次。将提示词构建暴露为独立能力，可让两类流程共享同一个 Agent 调用与回退
    策略，同时由各自调用方维持正确的图片数量和位置语义。
    """
    try:
        prompt_data = craft_prompt_fn(description, topic=topic, similarity="high")
        supplement = str((prompt_data or {}).get("prompt", "")).strip()
    except Exception as exc:
        logger.warning("参考图片提示词 Agent 失败，将使用规则提示词: %s", exc)
        supplement = ""

    if not supplement:
        try:
            supplement = str(fallback_prompt_fn(description, topic, "high") or "").strip()
        except Exception as exc:
            logger.warning("参考图片规则提示词构建失败: %s", exc)

    return compose_visual_imitation_prompt(
        description,
        subject=topic,
        supplement=supplement,
    )


def compose_visual_imitation_prompt(
    visual_description: Mapping[str, object],
    *,
    subject: str,
    supplement: str = "",
) -> str:
    """将视觉分析、新主体和补充描述合成为不可降级的最终生图提示词。

    内容 Agent 的补充描述不是最终提示词所有者，避免它返回空字符串或泛化文案时丢失
    参考图特征。该函数固定保留可用的构图、镜头、光影、色调和风格字段；新主体覆盖
    参考主体，同时固定追加无文字、无品牌和无二维码约束。
    """
    reference_subject = _text(visual_description.get("subject"))
    final_subject = _text(subject) or reference_subject or "与文章主题相关的新主体"
    parts = [f"主体：{final_subject}"]

    for label, field_name in (
        ("场景", "scene"),
        ("构图与版式", "composition"),
        ("镜头", "camera"),
        ("光影", "lighting"),
        ("色调", "color_palette"),
        ("视觉风格", "visual_style"),
        ("氛围", "mood"),
    ):
        value = _text(visual_description.get(field_name))
        if value:
            parts.append(f"{label}：{value}")

    details = visual_description.get("details")
    if isinstance(details, (list, tuple)):
        detail_text = "、".join(_text(item) for item in details[:3] if _text(item))
        if detail_text:
            parts.append(f"关键细节：{detail_text}")

    cleaned_supplement = _remove_reference_subject(supplement, reference_subject)
    if cleaned_supplement:
        parts.append(f"补充画面：{cleaned_supplement}")

    return (
        "，".join(parts)
        + "。高相似度还原参考图的构图、镜头、光影、色调与版式风格。"
        + "不要包含任何文字、品牌、水印、签名、标签或二维码。"
    )


def _text(value: object) -> str:
    """规范化视觉 Agent 的任意字段，防止空值和非字符串污染最终提示词。"""
    return " ".join(str(value or "").split())


def _remove_reference_subject(supplement: str, reference_subject: str) -> str:
    """避免内容 Agent 的补充描述把已替换的参考主体重新带回提示词。"""
    cleaned = _text(supplement)
    if len(reference_subject) >= 3:
        cleaned = cleaned.replace(reference_subject, "")
    return cleaned.strip(" ，、；：")
