"""视频镜头分析 Agent — 分析参考内容的镜头结构（时序、时长、运镜、转场）"""

import json
import logging
from typing import List, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

SHOT_ANALYSIS_PROMPT = """你是一个视频导演。请分析以下图片序列，规划视频的镜头结构。

一共有 {count} 张参考图片，按以下顺序排列：
{image_descriptions}

请分析：
1. 这些图片讲述了什么样的叙事节奏
2. 每张图片应该对应一个镜头
3. 每个镜头的时长分配（总时长约 {total_duration} 秒）
4. 每个镜头的运镜方式（静态/缓慢推近/缓慢拉远/横移）
5. 镜头之间的转场（硬切/淡入淡出）
6. 整体画面比例（横屏 16:9 / 竖屏 9:16）

输出 JSON 格式：
```json
{{
  "aspect_ratio": "9:16",
  "total_duration": {total_duration},
  "shots": [
    {{
      "shot_index": 1,
      "image_index": 0,
      "duration_sec": 3,
      "motion": "slow_zoom_in",
      "transition": "cut",
      "narrative": "这个镜头的作用"
    }}
  ]
}}
```

只输出 JSON，不要其他文字。"""


def analyze_shots(image_descriptions: List[str], total_duration: int = 12) -> Optional[dict]:
    """分析参考图片序列的镜头结构

    Args:
        image_descriptions: 每张图片的简短描述列表（按顺序）
        total_duration: 视频总时长（秒）

    Returns:
        {"aspect_ratio": str, "total_duration": int, "shots": [...]}
    """
    if not image_descriptions:
        return None

    try:
        llm = ChatOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.dashscope_model,
            temperature=0.4,
        )

        desc_text = "\n".join(
            f"图片 {i+1}: {desc}" for i, desc in enumerate(image_descriptions)
        )

        prompt = SHOT_ANALYSIS_PROMPT.format(
            count=len(image_descriptions),
            image_descriptions=desc_text,
            total_duration=total_duration,
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
        # 确保 shots 按 image_index 排序
        if "shots" in result:
            result["shots"].sort(key=lambda s: s.get("image_index", 0))
        return result

    except Exception as e:
        logger.warning("Shot analysis failed: %s", e)
        # 降级：每张图片一个镜头，均匀分配时长
        return {
            "aspect_ratio": "9:16",
            "total_duration": total_duration,
            "shots": [
                {
                    "shot_index": i + 1,
                    "image_index": i,
                    "duration_sec": max(total_duration // len(image_descriptions), 2),
                    "motion": "static",
                    "transition": "cut" if i > 0 else "none",
                    "narrative": "",
                }
                for i in range(len(image_descriptions))
            ],
        }
