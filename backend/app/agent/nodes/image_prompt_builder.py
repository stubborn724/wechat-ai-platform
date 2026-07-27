"""将结构化视觉描述转换为各图片模型的生成提示词"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 默认负向提示词（适用于大多数模型）
DEFAULT_NEGATIVE_PROMPT = (
    "文字, 水印, 签名, 标签, 二维码, 扭曲的手指, 多余的手指, "
    "畸形的手, 变形的脸, 模糊, 低质量, 噪点, 过度曝光"
)


def build_wanxiang_prompt(desc: dict, topic: str = "", similarity: str = "medium") -> str:
    """构建通义万相（Wanxiang）的图片生成提示词

    Args:
        desc: 结构化视觉描述
        topic: 主题/话题
        similarity: 相似度 low/medium/high

    Returns:
        生成提示词
    """
    parts = []

    # 主体
    if desc.get("subject"):
        parts.append(desc["subject"])

    # 场景
    if desc.get("scene"):
        parts.append(desc["scene"])

    # 构图（高/中相似度时保留）
    if similarity in ("medium", "high") and desc.get("composition"):
        parts.append(f"构图：{desc['composition']}")

    # 镜头（高相似度时保留）
    if similarity == "high" and desc.get("camera"):
        parts.append(f"镜头：{desc['camera']}")

    # 光线
    if similarity in ("medium", "high") and desc.get("lighting"):
        parts.append(f"光线：{desc['lighting']}")

    # 色调
    if similarity in ("medium", "high") and desc.get("color_palette"):
        parts.append(f"色调：{desc['color_palette']}")

    # 视觉风格
    if desc.get("visual_style"):
        parts.append(f"风格：{desc['visual_style']}")

    # 氛围
    if desc.get("mood"):
        parts.append(f"氛围：{desc['mood']}")

    # 细节
    if similarity == "high" and desc.get("details"):
        detail_text = "，".join(desc["details"][:3])
        if detail_text:
            parts.append(f"包含：{detail_text}")

    prompt = "，".join(parts) if parts else topic or "高质量图片"

    # 追加无文字指令（硬性规定）
    prompt += "。不要包含任何文字、字母、数字或文本标签，纯图像。"

    return prompt


def build_prompt(desc: dict, topic: str = "", similarity: str = "medium", model: str = "wanxiang") -> dict:
    """构建标准化的提示词结构（适配不同模型）

    Args:
        desc: 结构化视觉描述
        topic: 主题
        similarity: 相似度 low/medium/high
        model: 目标模型

    Returns:
        {"prompt": str, "negative_prompt": str, ...}
    """
    if model == "wanxiang":
        prompt = build_wanxiang_prompt(desc, topic, similarity)
    else:
        prompt = build_wanxiang_prompt(desc, topic, similarity)

    return {
        "prompt": prompt,
        "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
        "similarity": similarity,
        "source_index": desc.get("image_index", 0),
        "motion": desc.get("motion", ""),
        "duration_sec": desc.get("duration_sec", 3),
    }
