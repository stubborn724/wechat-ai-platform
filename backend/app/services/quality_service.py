"""文章质量评分服务 — 使用 DashScope 多维度评估"""

import hashlib
import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import Article

logger = logging.getLogger(__name__)

# Prompt 版本，用于评分追溯
PROMPT_VERSION = "v1.0"
MODEL_NAME = "qwen-plus"

# 评分权重
WEIGHTS = {
    "content_score": 0.20,
    "readability_score": 0.15,
    "structure_score": 0.15,
    "value_score": 0.20,
    "title_score": 0.10,
    "title_consistency_score": 0.10,
    "credibility_score": 0.10,
}

QUALITY_PROMPT = """你是一个专业的微信公众号文章质量评估专家。请对以下文章进行多维度评分。

文章标题：{title}

文章正文：
{content}

请从以下 7 个维度打分（0-100），并输出结构化 JSON。

评分标准：
1. content_score (内容完整性)：内容是否充分、有明确观点和论据，20% 权重
2. readability_score (可读性)：表达是否流畅清晰易于理解，15% 权重
3. structure_score (结构清晰度)：标题/段落/层级/逻辑是否合理，15% 权重
4. value_score (用户价值)：能否解决问题或提供有效信息，20% 权重
5. title_score (标题吸引力)：标题是否具有阅读吸引力，10% 权重
6. title_consistency_score (标题正文一致性)：标题是否准确反映正文内容，10% 权重
7. credibility_score (内容可信度)：是否存在事实风险/夸大/逻辑问题，10% 权重

同时分析存在的问题，给出优化建议。

输出格式（必须严格 JSON，不要包含其他文字）：
{{
    "content_score": 78,
    "readability_score": 82,
    "structure_score": 65,
    "value_score": 72,
    "title_score": 58,
    "title_consistency_score": 88,
    "credibility_score": 80,
    "issues": [
        {{"type": "structure", "severity": "medium", "description": "正文段落过长，缺少小标题"}}
    ],
    "suggestions": ["增加二级标题", "在开头明确文章能解决的问题"],
    "rewrite_recommended": true,
    "rewrite_scope": "title_and_structure",
    "factual_risk": "low",
    "brand_risk": "low",
    "confidence": 0.86
}}
"""


class ArticleQualityService:
    """AI 文章质量评分服务"""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        return self._client

    def _compute_content_hash(self, article: Article) -> str:
        """计算文章内容哈希用于去重"""
        raw = f"{article.main_title or ''}{article.full_content or article.content or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _prepare_content(self, article: Article) -> str:
        """准备评分用的正文内容"""
        text = article.full_content or article.content or ""
        # 去除 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 去除 Markdown 图片标记
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        # 去除多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 截断长文（保留前 4000 字 + 后 1000 字）
        if len(text) > 6000:
            text = text[:4000] + "\n\n[...中略...]\n\n" + text[-1000:]
        return text.strip()

    async def evaluate(self, article: Article) -> dict:
        """对文章执行质量评分"""
        content = self._prepare_content(article)
        title = article.main_title or article.topic or ""

        prompt = QUALITY_PROMPT.format(title=title, content=content)

        try:
            client = self._get_client()
            resp = await client.chat.completions.create(
                model=settings.dashscope_model or MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:
            raise RuntimeError(f"DashScope call failed: {exc}") from exc

        result = self._parse_and_validate(raw)
        result["model_name"] = settings.dashscope_model or MODEL_NAME
        result["model_version"] = MODEL_NAME
        result["prompt_version"] = PROMPT_VERSION
        result["input_content_hash"] = self._compute_content_hash(article)
        result["raw_response"] = {"raw": raw}
        result["overall_score"] = self._calculate_overall(result)
        return result

    def _parse_and_validate(self, raw: str) -> dict:
        """解析和校验模型输出"""
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Model output is not valid JSON")

        # 校验必填字段
        required = [
            "content_score", "readability_score", "structure_score",
            "value_score", "title_score", "title_consistency_score",
            "credibility_score",
        ]
        for field in required:
            if field not in result:
                raise ValueError(f"Missing required field: {field}")
            val = result[field]
            if not isinstance(val, (int, float)) or val < 0 or val > 100:
                result[field] = max(0, min(100, int(val)))

        # 补充字段默认值
        result.setdefault("issues", [])
        result.setdefault("suggestions", [])
        result.setdefault("rewrite_recommended", False)
        result.setdefault("factual_risk", "low")
        result.setdefault("brand_risk", "low")
        result.setdefault("confidence", 0.8)

        return result

    def _calculate_overall(self, scores: dict) -> int:
        """按权重计算总分"""
        total = 0
        for dim, weight in WEIGHTS.items():
            val = scores.get(dim, 0)
            if isinstance(val, (int, float)):
                total += val * weight
        return round(total)


# 全局实例
quality_service = ArticleQualityService()
