"""仿写标题 Agent — 分析参考标题风格，生成相似风格的新标题"""

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

TITLE_IMITATION_PROMPT = """你是一个标题仿写专家。请参考以下标题的写作风格，生成与主题相关的新标题。

参考标题：{reference_title}
主题：{topic}

请分析参考标题的以下维度：
1. 句式结构（陈述句/疑问句/感叹句/省略句等）
2. 字数规律
3. 用词风格（正式/口语/文艺/夸张等）
4. 标点使用习惯
5. 常用的修辞手法

然后生成 {count} 个新标题，要求：
- 写作风格模仿参考标题
- 内容贴近主题「{topic}」
- 每个标题独立一行
- 不要编号
- 直接输出标题文本

只输出标题，不要其他内容。"""


def imitate_title(reference_title: str, topic: str = "", count: int = 3) -> list[str]:
    """仿写标题

    Args:
        reference_title: 参考标题
        topic: 主题/话题（仿写标题需围绕此主题）
        count: 生成数量

    Returns:
        新标题列表
    """
    print(f"\n{'='*50}")
    print(f"  [Agent 1] 标题仿写")
    print(f"  ├─ 输入参考标题: {reference_title}")
    print(f"  ├─ 主题: {topic}")
    if not reference_title:
        print(f"  └─ 输出: [空]（无参考标题）")
        print(f"{'='*50}")
        return [topic or ""]

    try:
        llm = ChatOpenAI(
            api_key=settings.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.dashscope_model,
            temperature=0.7,
        )

        prompt = TITLE_IMITATION_PROMPT.format(
            reference_title=reference_title,
            topic=topic or "无主题",
            count=count,
        )

        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content
        if isinstance(text, list):
            parts = [b["text"] for b in text if isinstance(b, dict) and b.get("text")]
            text = "".join(parts)

        titles = [t.strip().strip('"').strip("'") for t in text.strip().split("\n") if t.strip()]
        titles = titles[:count]
        print(f"  ├─ LLM prompt: {prompt[:120]}...")
        print(f"  └─ 输出标题: {titles}")
        print(f"{'='*50}")
        return titles if titles else [reference_title]

    except Exception as e:
        logger.warning("Title imitation failed: %s", e)
        return [reference_title]
