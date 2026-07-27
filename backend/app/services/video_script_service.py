"""视频脚本生成服务 — LLM 输出结构化脚本/分镜 JSON"""

import json
import logging
import re
from typing import List, Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """从 LLM 响应中提取 JSON 字符串（去掉 markdown 代码块包裹）"""
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text.strip()


class StoryboardItem:
    """单个分镜"""
    def __init__(self, seq: int, duration_sec: int,
                 visual_desc: str, narration: str, subtitle: str,
                 image_prompt: str, transition: str = "fade"):
        self.seq = seq
        self.duration_sec = duration_sec
        self.visual_desc = visual_desc
        self.narration = narration
        self.subtitle = subtitle
        self.image_prompt = image_prompt
        self.transition = transition


class VideoScriptResult:
    """视频脚本结构化输出"""
    def __init__(self, title: str, hook: str, narration: str,
                 storyboards: Optional[List[StoryboardItem]] = None,
                 cta: str = ""):
        self.title = title
        self.hook = hook
        self.narration = narration
        self.storyboards = storyboards or []
        self.cta = cta


VIDEO_SCRIPT_PROMPT = """你是一个专业短视频策划。根据用户主题生成视频脚本和分镜。

配置：
- 视频时长：{total_duration} 秒
- 分镜数量：{storyboard_count} 个
- 画面比例：{aspect_ratio}

要求：
1. 开头 3 秒要抓人眼球（提问/数据/冲突）
2. 中间围绕主题展开，有逻辑递进
3. 结尾明确行动引导
4. 总时长 = 各分镜时长之和
5. 每个分镜包含画面描述、配音、字幕和文生图提示词

输出 JSON 格式（不要 markdown 代码块）：
{{
  "title": "视频标题",
  "hook": "开场吸引语",
  "narration": "完整解说词",
  "cta": "结尾行动引导语",
  "storyboards": [
    {{
      "seq": 1,
      "duration_sec": 5,
      "visual_desc": "画面描述",
      "narration": "这段的配音文字",
      "subtitle": "同步字幕文字",
      "image_prompt": "英文文生图提示词，用于生成该分镜背景图",
      "transition": "fade"
    }}
  ]
}}

用户主题：{topic}
目标用户：{target_audience}
品牌风格：{brand_style}
补充说明：{extra_notes}
"""


async def generate_video_script(
    topic: str,
    total_duration: int = 30,
    storyboard_count: int = 5,
    aspect_ratio: str = "9:16",
    target_audience: str = "",
    brand_style: str = "专业",
    extra_notes: str = "",
) -> VideoScriptResult:
    """生成视频脚本和分镜"""
    if not settings.dashscope_api_key:
        # 无 API key 返回占位脚本
        duration_per = max(3, total_duration // storyboard_count)
        storyboards = []
        for i in range(storyboard_count):
            storyboards.append(StoryboardItem(
                seq=i + 1,
                duration_sec=duration_per if i < storyboard_count - 1
                           else total_duration - duration_per * (storyboard_count - 1),
                visual_desc=f"关于{topic}的画面{i + 1}",
                narration=f"这是关于{topic}的第{i + 1}部分内容。",
                subtitle=f"第{i + 1}部分",
                image_prompt=f"A beautiful scene about {topic}, professional style",
            ))
        return VideoScriptResult(
            title=topic,
            hook=f"今天我们来聊聊{topic}",
            narration=" ".join(f"这是关于{topic}的第{i+1}部分内容。" for i in range(storyboard_count)),
            storyboards=storyboards,
            cta="关注我们获取更多信息",
        )

    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.dashscope_model,
            messages=[
                {"role": "system", "content": "你是一个专业短视频策划和脚本撰写专家。"},
                {"role": "user", "content": VIDEO_SCRIPT_PROMPT.format(
                    topic=topic,
                    total_duration=total_duration,
                    storyboard_count=storyboard_count,
                    aspect_ratio=aspect_ratio,
                    target_audience=target_audience,
                    brand_style=brand_style,
                    extra_notes=extra_notes,
                )},
            ],
            temperature=0.6,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(_extract_json(raw))

        storyboards = []
        for sb in data.get("storyboards", []):
            storyboards.append(StoryboardItem(
                seq=sb.get("seq", 1),
                duration_sec=sb.get("duration_sec", 5),
                visual_desc=sb.get("visual_desc", ""),
                narration=sb.get("narration", ""),
                subtitle=sb.get("subtitle", ""),
                image_prompt=sb.get("image_prompt", ""),
                transition=sb.get("transition", "fade"),
            ))

        # 校验总时长是否匹配
        total = sum(s.duration_sec for s in storyboards)
        if total != total_duration and storyboards:
            # 调整最后一个分镜时长
            diff = total_duration - total
            storyboards[-1].duration_sec += diff

        return VideoScriptResult(
            title=data.get("title", topic)[:50],
            hook=data.get("hook", ""),
            narration=data.get("narration", ""),
            storyboards=storyboards,
            cta=data.get("cta", ""),
        )
    except Exception as exc:
        logger.warning("Video script generation failed: %s", exc)
        return await generate_video_script(topic)  # fallback with no API key
