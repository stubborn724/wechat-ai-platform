"""理解参考图片，输出结构化视觉描述（纯图片/视频仿写用）"""

import json
import logging
from typing import List, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

VISUAL_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "image_index": {"type": "integer"},
        "subject": {"type": "string", "description": "图片主体描述"},
        "scene": {"type": "string", "description": "场景/背景描述"},
        "composition": {"type": "string", "description": "构图方式"},
        "camera": {"type": "string", "description": "镜头语言"},
        "lighting": {"type": "string", "description": "光线"},
        "color_palette": {"type": "string", "description": "色调特征"},
        "visual_style": {"type": "string", "description": "视觉风格"},
        "details": {"type": "array", "items": {"type": "string"}, "description": "细节元素"},
        "mood": {"type": "string", "description": "氛围/情绪"},
        "motion": {"type": "string", "description": "运镜方式"},
        "duration_sec": {"type": "integer", "description": "镜头时长"},
        "is_qrcode": {"type": "boolean", "description": "该图片是否为二维码/条形码/带有明显营销二维码"},
    },
    "required": [
        "image_index", "subject", "scene", "composition", "camera",
        "lighting", "color_palette", "visual_style", "details", "mood", "is_qrcode",
    ],
}

UNDERSTAND_PROMPT = """你是一个专业的视觉分析师。请分析这张图片，输出结构化的视觉描述。

要求：
1. 仔细观察图片的主体、背景、构图、光线、颜色、风格
2. 识别图片的类型（实拍照片 / AI生成 / 插画 / 设计排版等）
3. 注意值得保留的构图特征和视觉元素
4. 如果图片有明显的水印、文字或品牌标识，在 details 中注明
5. 重点判断图片是否为二维码/条形码/带有明显营销二维码，如果是则设置 is_qrcode 为 true

输出的 JSON 必须严格遵循以下 schema：
```json
{
  "image_index": <序号>,
  "subject": "主体描述",
  "scene": "场景描述",
  "composition": "构图方式",
  "camera": "镜头语言",
  "lighting": "光线描述",
  "color_palette": "色调特征",
  "visual_style": "视觉风格",
  "details": ["细节1", "细节2"],
  "mood": "氛围情绪",
  "is_qrcode": false
}
```

只输出 JSON，不要其他文字。"""


def understand_image(url: str, index: int) -> Optional[dict]:
    """分析单张图片，返回结构化视觉描述"""
    print(f"\n  [Agent 3] 视觉理解 图片 {index+1}")
    print(f"  ├─ URL: {url[:80]}")
    try:
        llm = ChatOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-vl-max",
            temperature=0.3,
            max_tokens=1024,
        )

        content = [
            {"type": "text", "text": UNDERSTAND_PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]

        response = llm.invoke([HumanMessage(content=content)])
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
        result["image_index"] = index
        print(f"  ├─ 主体: {result.get('subject', '')[:60]}")
        print(f"  ├─ 场景: {result.get('scene', '')[:60]}")
        print(f"  ├─ 风格: {result.get('visual_style', '')[:40]}")
        print(f"  └─ 色调: {result.get('color_palette', '')[:40]}")
        return result

    except Exception as e:
        logger.warning("Failed to understand image %s: %s", url[:60], e)
        print(f"  └─ 失败: {e}")
        return {
            "image_index": index,
            "subject": "", "scene": "", "composition": "",
            "camera": "", "lighting": "", "color_palette": "",
            "visual_style": "", "details": [], "mood": "",
        }


def understand_images(urls: List[str]) -> List[dict]:
    """批量分析多张图片"""
    print(f"\n{'='*50}")
    print(f"  [Agent 3] 批量视觉理解 共 {len(urls)} 张图")
    print(f"{'='*50}")
    results = []
    for i, url in enumerate(urls):
        desc = understand_image(url, i)
        results.append(desc)
    print(f"{'='*50}")
    print(f"  Agent 3 完成: {len(results)} 张")
    print(f"{'='*50}")
    return results
