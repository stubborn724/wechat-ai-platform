"""文章结构深度拆解 Agent

深入分析目标文章的:
  - 标题模式（数字型/悬念型/痛点型/热点型）
  - 开头钩子（故事型/数据型/提问型/金句型）
  - 段落节奏（短句密度/段落长度分布）
  - 爆点模式（金句/转折/冲突）
  - 结尾套路（总结型/引导型/悬念型）
  - 排版特征（配图位置/引用/加粗/emoji 使用）
  - 互动引导（评论区引导/在看/转发）

输出结构化 StyleProfile 用于后续仿写。
"""

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.constants.prompt import PromptConstant

logger = logging.getLogger(__name__)

STRUCTURE_ANALYSIS_PROMPT = """你是一个专业的公众号文章结构分析专家。请深度分析以下文章，拆解其内容结构和写作手法。

## 文章内容
{content}

## 分析要求
请从以下维度进行深度拆解，输出 JSON：

1. **title_pattern**: 标题模式 (number_list/curiosity/pain_point/hot_topic/question/how_to/contrast)
2. **title_feature**: 标题特征描述
3. **hook_type**: 开头钩子类型 (story/data/question/quote/scene/direct)
4. **hook_description**: 开头钩子写法描述
5. **paragraph_rhythm**: 段落节奏分析
   - avg_sentence_length: 平均句长（字符数）
   - short_sentence_ratio: 短句占比（<20字符）
   - paragraph_length_distribution: 段落长度分布 ["short", "medium", "long"] 占比
6. **engagement_patterns**: 爆点/互动模式
   - golden_sentences: 金句示例列表
   -转折点位置: 文中情绪或观点转折的位置
   -互动引导: 引导读者评论/转发的具体话术
7. **closing_style**: 结尾风格 (summary/guidance/question/call_to_action/suspense)
8. **formatting_features**: 排版特征
   - emoji_usage: list of emoji used
   - bold_count: 加粗使用次数
   - blockquote_count: 引用使用次数
   - image_placement: 配图位置分布（开头/中间/末尾）
   - has_toc: 是否有目录
9. **content_structure**: 内容结构
   - sections: 章节数
   - structure_type: 结构类型 (l-linear/list-style/problem-solution/story-telling/scene-based)
10. **vocabulary_style**: 词汇风格 (professional/colloquial/neutral/warm/humorous)
11. **target_audience**: 目标受众描述
12. **overall_score**: 综合质量评分 1-10
13. **key_takeaways**: 可模仿的核心要点列表

## 输出格式
请严格按以下 JSON 格式输出，不要包含其他内容：
{{
  "title_pattern": "curiosity",
  "title_feature": "使用疑问句式制造好奇心缺口",
  "hook_type": "story",
  "hook_description": "以第一人称亲身经历开头，制造代入感",
  "paragraph_rhythm": {{
    "avg_sentence_length": 35,
    "short_sentence_ratio": 0.4,
    "paragraph_length_distribution": ["short", "medium", "medium", "long", "short"]
  }},
  "engagement_patterns": {{
    "golden_sentences": ["金句1", "金句2"],
    "转折点位置": ["约在文章40%处出现观点反转"],
    "互动引导": ["末尾引导评论: '你觉得呢？评论区告诉我'"]
  }},
  "closing_style": "guidance",
  "formatting_features": {{
    "emoji_usage": ["🔥", "💡"],
    "bold_count": 5,
    "blockquote_count": 2,
    "image_placement": ["开头", "中间", "末尾"],
    "has_toc": false
  }},
  "content_structure": {{
    "sections": 5,
    "structure_type": "problem-solution"
  }},
  "vocabulary_style": "warm",
  "target_audience": "25-40岁职场人",
  "overall_score": 8,
  "key_takeaways": ["使用故事开头引发共鸣", "每300字插入一个金句"]
}}"""


class StructureAnalysisResult:
    """结构分析结果的数据封装"""

    def __init__(self, data: dict):
        self.data = data

    @property
    def title_pattern(self) -> str:
        return self.data.get("title_pattern", "")

    @property
    def hook_type(self) -> str:
        return self.data.get("hook_type", "")

    @property
    def closing_style(self) -> str:
        return self.data.get("closing_style", "")

    @property
    def key_takeaways(self) -> list:
        return self.data.get("key_takeaways", [])

    @property
    def overall_score(self) -> int:
        return self.data.get("overall_score", 5)

    def to_prompt_section(self) -> str:
        """将结构分析结果转换为 Prompt 指令段，注入仿写生成"""
        lines = ["\n## 📐 仿写结构指南（请严格按照以下结构特征来撰写）\n"]

        lines.append(f"### 标题模式\n类型: {self.title_pattern}")
        title_feature = self.data.get("title_feature", "")
        if title_feature:
            lines.append(f"特征: {title_feature}")

        lines.append(f"\n### 开头写法\n类型: {self.hook_type}")
        hook_desc = self.data.get("hook_description", "")
        if hook_desc:
            lines.append(f"写法: {hook_desc}")

        rhythm = self.data.get("paragraph_rhythm", {})
        if rhythm:
            lines.append(f"\n### 段落节奏")
            lines.append(f"- 平均句长: ~{rhythm.get('avg_sentence_length', 'N/A')} 字符")
            lines.append(f"- 段落分布: {' → '.join(rhythm.get('paragraph_length_distribution', []))}")

        eng = self.data.get("engagement_patterns", {})
        if eng:
            lines.append(f"\n### 爆点与互动")
            golden = eng.get("golden_sentences", [])
            if golden:
                lines.append(f"- 金句参考: {golden[0]}")
            engagement = eng.get("互动引导", [])
            if engagement:
                lines.append(f"- 互动引导: {engagement[0]}")

        lines.append(f"\n### 结尾风格\n{self.closing_style}")

        formatting = self.data.get("formatting_features", {})
        if formatting:
            emoji = formatting.get("emoji_usage", [])
            if emoji:
                lines.append(f"\n### 排版特征")
                lines.append(f"- 使用 emoji: {' '.join(emoji)}")
                lines.append(f"- 加粗频率: ~{formatting.get('bold_count', 0)} 次/篇")

        content_struct = self.data.get("content_structure", {})
        if content_struct:
            lines.append(f"\n### 内容结构\n类型: {content_struct.get('structure_type', '')}")

        lines.append(f"\n### 核心仿写要点")
        for i, t in enumerate(self.key_takeaways, 1):
            lines.append(f"{i}. {t}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return self.data


def analyze_article_structure(content: str) -> Optional[StructureAnalysisResult]:
    """分析单篇文章结构，返回结构分析结果"""
    if not content or len(content.strip()) < 100:
        logger.warning("Content too short for structure analysis")
        return None

    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model=settings.dashscope_model,
        temperature=0.3,
    )

    prompt = STRUCTURE_ANALYSIS_PROMPT.format(content=content[:6000])

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))

        data = json.loads(raw)
        # Handle nested structure
        if isinstance(data, dict) and any(k in data for k in [
            "title_pattern", "hook_type", "closing_style"
        ]):
            return StructureAnalysisResult(data)

        # Try to unwrap
        for key in ["analysis", "result", "structure_analysis"]:
            if key in data:
                return StructureAnalysisResult(data[key])

        logger.warning("Structure analysis returned unexpected format: %s", list(data.keys())[:5])
        return StructureAnalysisResult(data)

    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Structure analysis failed: %s", exc)
        return None


def analyze_articles_batch(contents: list) -> Optional[StructureAnalysisResult]:
    """合并分析多篇文章，提取共性结构特征

    适用于一个 FeedSource 有多篇文章时，综合提取风格模式。
    """
    if not contents:
        return None

    combined = "\n\n--- 下一篇 ---\n\n".join(c[:2000] for c in contents if c)
    return analyze_article_structure(combined)
