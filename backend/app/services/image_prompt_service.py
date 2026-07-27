"""图片文案生成服务 — LLM 输出结构化文案 JSON，不做文件 IO"""

import json
import logging
import re
from typing import List, Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# 缓存客户端（与 article_agent_service 一致）
_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


def _extract_json(text: str) -> str:
    """从 LLM 响应中提取 JSON 字符串（去掉 markdown 代码块包裹）"""
    # 去掉 ```json ... ``` 包裹
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text)
    # 查找第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text.strip()


class ImageCopywritingResult:
    """图片文案结构化输出"""
    def __init__(self, main_title: str = "", sub_title: str = "",
                 selling_points: Optional[List[str]] = None,
                 cta: str = "", disclaimer: str = "",
                 style_description: str = ""):
        self.main_title = main_title
        self.sub_title = sub_title
        self.selling_points = selling_points or []
        self.cta = cta
        self.disclaimer = disclaimer
        self.style_description = style_description


COPYWRITING_PROMPT = """你是一个专业营销文案策划。根据用户提供的主题和产品信息，生成适合宣传海报的文案。

要求：
- 主标题：简短醒目，不超过 20 个汉字
- 副标题：补充核心价值或优惠信息
- 卖点列表：3-5 条，每条不超过 15 字
- 行动引导语：明确告诉用户下一步操作
- 免责声明：如有必要，简洁说明
- 风格描述：说明适合的背景图风格（如"科技感蓝色渐变"、"温暖生活场景"）

输出 JSON 格式（不要 markdown 代码块），例如：{{'main_title': '标题', 'sub_title': '副标题'}}

用户主题：{topic}
品牌风格：{brand_style}
目标用户：{target_audience}
"""


IMAGE_PROMPT_PROMPT = """根据以下文案风格描述，生成一段文生图提示词（英文）。

要求：
- 提示词只描述背景画面，不能包含任何文字、汉字、字符
- 适合作为海报背景图，要有留白区域放置文字
- 比例：{aspect_ratio}
- 画面干净、高级感，适合商业营销

风格描述：{style_description}
产品/主题：{topic}

输出只有纯文本提示词，不要多余内容。"""


async def generate_copywriting(
    topic: str,
    brand_style: str = "简约现代",
    target_audience: str = "",
) -> ImageCopywritingResult:
    """根据主题生成海报文案"""
    if not settings.dashscope_api_key:
        # 无 API key 时返回占位文案
        return ImageCopywritingResult(
            main_title=topic,
            sub_title="探索更多精彩内容",
            selling_points=["品质保证", "专业服务", "值得信赖"],
            cta="立即咨询",
            style_description="简约现代风格，干净留白",
        )

    client = _get_client()

    try:
        resp = await client.chat.completions.create(
            model=settings.dashscope_model,
            messages=[
                {"role": "system", "content": "你是一个专业的营销文案策划专家。请严格按照 JSON 格式输出，不要使用 markdown 代码块。"},
                {"role": "user", "content": COPYWRITING_PROMPT.format(
                    topic=topic,
                    brand_style=brand_style,
                    target_audience=target_audience,
                )},
            ],
            temperature=0.5,
        )
        content = resp.choices[0].message.content or "{}"
        logger.debug("Raw copywriting response: %s", content[:500])
        # 直接从响应中提取 JSON
        data = json.loads(_extract_json(content))
        return ImageCopywritingResult(
            main_title=data.get("main_title", topic)[:20],
            sub_title=data.get("sub_title", "")[:30],
            selling_points=data.get("selling_points", [])[:5],
            cta=data.get("cta", ""),
            disclaimer=data.get("disclaimer", ""),
            style_description=data.get("style_description", "简约现代"),
        )
    except Exception as exc:
        import traceback
        logger.warning("Copywriting LLM call failed: type=%s msg=%s\n%s",
                       type(exc).__name__, exc, traceback.format_exc())
        return ImageCopywritingResult(main_title=topic)


async def generate_image_prompt(
    style_description: str,
    topic: str,
    aspect_ratio: str = "3:4",
) -> str:
    """根据文案风格描述生成文生图提示词（不含文字）"""
    if not settings.dashscope_api_key:
        return f"A clean background for {topic}, modern style, empty space for text"

    client = _get_client()

    try:
        resp = await client.chat.completions.create(
            model=settings.dashscope_model,
            messages=[
                {"role": "system", "content": "你是一个 AI 绘画提示词工程师。"},
                {"role": "user", "content": IMAGE_PROMPT_PROMPT.format(
                    style_description=style_description,
                    topic=topic,
                    aspect_ratio=aspect_ratio,
                )},
            ],
            temperature=0.5,
        )
        prompt = (resp.choices[0].message.content or "").strip()
        return prompt
    except Exception as exc:
        logger.warning("Image prompt LLM call failed: type=%s msg=%s", type(exc).__name__, exc)
        return f"A clean background for {topic}"
