"""提示词构建 Agent — 根据视觉理解和镜头分析，智能生成图片生成提示词"""

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_CRAFTING_PROMPT = """你是一个 AI 图片提示词工程师。请根据以下视觉分析结果，生成图片生成模型的提示词。

视觉分析结果：
```json
{visual_desc}
```

生成要求：
1. 提示词要完整描述画面的主体、场景、构图、光线、色调、风格
2. 用自然语言描述，用逗号分隔关键要素
3. 如果要求 "高相似度"，尽可能保留原图的构图和视觉特征
4. 如果要求 "低相似度"，只保留主题和内容类型，改变构图和风格
5. 不要描述图片中不存在的元素
6. 注意：主体不能包含任何具体人物、品牌标识或受版权保护的内容
7. **硬性规定：图片中绝对不能包含任何文字、字母、数字、文本、标签、标题或符号**

相似度级别：{similarity}
主题：{topic}

输出 JSON 格式：
```json
{{
  "prompt": "完整的图片生成提示词，用逗号分隔",
  "negative_prompt": "需要避免的元素列表，用逗号分隔",
  "style_notes": "风格说明"
}}
```

如果这是视频的一个镜头，运镜方式为 "{motion}"，请在 prompt 中考虑画面构图适合该运镜。

只输出 JSON，不要其他文字。"""

DEFAULT_NEGATIVE = (
    "文字, 水印, 签名, 标签, 二维码, 扭曲的手指, 多余的手指, "
    "畸形的手, 变形的脸, 模糊, 低质量, 噪点, 过度曝光"
)


def craft_prompt(
    visual_desc: dict,
    topic: str = "",
    similarity: str = "medium",
    motion: str = "",
    duration_sec: int = 3,
) -> dict:
    """LLM 智能构建生成提示词"""
    print(f"\n  [Agent 4] 提示词构建")
    print(f"  ├─ 主体: {visual_desc.get('subject', '')[:50]}")
    print(f"  ├─ 相似度: {similarity}")
    print(f"  ├─ 运镜: {motion}")
    try:
        llm = ChatOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.dashscope_model,
            temperature=0.5,
        )

        prompt = PROMPT_CRAFTING_PROMPT.format(
            visual_desc=json.dumps(visual_desc, ensure_ascii=False, indent=2),
            similarity=similarity,
            topic=topic or "",
            motion=motion or "static",
        )

        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content
        if isinstance(text, list):
            parts = [b["text"] for b in text if isinstance(b, dict) and b.get("text")]
            text = "".join(parts)

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        result = json.loads(text)
        if "prompt" not in result:
            result["prompt"] = ""
        gen_prompt = result.get("prompt", "")
        print(f"  生成 prompt ({len(gen_prompt)}字): {gen_prompt[:200]}")
        return {
            "prompt": gen_prompt,
            "negative_prompt": result.get("negative_prompt", DEFAULT_NEGATIVE),
            "style_notes": result.get("style_notes", ""),
            "motion": motion,
            "duration_sec": duration_sec,
        }

    except Exception as e:
        logger.warning("Prompt crafting failed, fallback to rule-based: %s", e)
        print(f"  LLM 失败: {e} → 回退规则拼接")
        from app.agent.nodes.image_prompt_builder import build_wanxiang_prompt
        prompt_text = build_wanxiang_prompt(visual_desc, topic, similarity)
        print(f"  回退 prompt ({len(prompt_text)}字): {prompt_text[:200]}")
        return {
            "prompt": prompt_text,
            "negative_prompt": DEFAULT_NEGATIVE,
            "style_notes": "",
            "motion": motion,
            "duration_sec": duration_sec,
        }


# ============================================================================
# 视频 prompt 构建
# ============================================================================

VIDEO_PROMPT_CRAFTING_PROMPT = """你是一个 AI 视频提示词工程师。请根据以下视频规划，生成视频生成模型的提示词。

视频规划：
```json
{video_plan}
```

生成要求：
1. 提示词要完整描述整个视频的内容，包含所有场景的连贯叙事
2. 每个场景的画面风格要保持一致
3. 描述整体的色调、氛围和视觉风格
4. 注意画面比例和镜头语言
5. 不要包含任何具体人物姓名、品牌标识或受版权保护的内容

输出 JSON 格式：
```json
{{
  "prompt": "完整的视频生成提示词，描述整个视频的内容和风格",
  "style": "统一的视觉风格描述",
  "negative_prompt": "需要避免的元素"
}}
```

只输出 JSON，不要其他文字。"""
