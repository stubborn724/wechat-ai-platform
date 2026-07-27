"""文章优化服务 — 支持 10 种优化类型，生成优化稿"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import Article, ArticleOptimization, ArticleQualityEvaluation

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0"

# 各优化类型的 Prompt 模板

PROMPTS = {
    "title_optimize": """你是一个专业的公众号文章标题优化专家。
请根据原文内容，优化文章标题。

原文标题：{title}
正文开头：{opening}

要求：
1. 生成 5 个优化标题候选
2. 保持标题与正文内容一致
3. 避免标题党
4. 在标题中尽量包含具体数字或收益点
5. 给出你推荐的标题及理由

输出 JSON 格式：
{{"candidates": ["标题1", "标题2", "标题3", "标题4", "标题5"], "recommended": "标题1", "reason": "选择理由"}}
""",

    "opening_optimize": """你是一个专业的公众号文章优化专家。
请优化以下文章的开头部分（前 200 字），要求：
1. 保留原文核心信息
2. 在首段明确读者能获得的收益
3. 增加吸引力，让读者愿意继续阅读
4. 保持语言风格一致

原文标题：{title}
原文开头：{opening}
正文后续内容：{rest}

输出完整优化后的开头段落，以及修改说明的 JSON：
{{"optimized_opening": "...", "change_summary": "..."}}
""",

    "structure_optimize": """你是一个专业的公众号文章结构优化专家。
请分析并优化以下文章的结构：
1. 保留所有核心事实和观点
2. 重组段落顺序使逻辑更清晰
3. 增加或调整小标题
4. 确保段落长度适中

原文：{full_content}

输出完整优化后的全文及修改说明的 JSON：
{{"optimized_content": "...", "changes": [{"type": "结构调整", "detail": "..."}]}}
""",

    "readability_optimize": """你是一个专业的公众号文章可读性优化专家。
请优化以下文章的可读性：
1. 将长句拆分为短句
2. 用更通俗的词汇替换专业术语（保留必要的行业术语）
3. 增加过渡句使段落衔接自然
4. 保持原文核心信息和观点不变

原文：{full_content}

输出 JSON：
{{"optimized_content": "...", "changes": [{"type": "readability", "detail": "..."}]}}
""",

    "content_expand": """你是一个专业的公众号文章内容扩充专家。
请在保留原有结构和观点的基础上扩充内容：
1. 每个主要段落扩充约 30% 内容
2. 增加具体案例、数据或论据
3. 不虚构事实和数据
4. 增加的內容必须与原文观点一致

原文：{full_content}

输出 JSON：
{{"optimized_content": "...", "expansion_notes": "..."}}
""",

    "content_condense": """你是一个专业的公众号文章精简专家。
请精简以下文章：
1. 保留核心观点和关键信息
2. 删减冗余表达和重复内容
3. 压缩到原文约 70% 长度
4. 保持可读性和流畅度

原文：{full_content}

输出 JSON：
{{"optimized_content": "...", "changes": [{"type": "condense", "detail": "..."}]}}
""",

    "value_enhance": """你是一个专业的公众号文章价值增强专家。
请在不改变原文事实的前提下增强文章的用户价值：
1. 在每个主要观点后增加"这对读者意味着什么"部分
2. 增加实用建议或行动指南
3. 强化文章的实用性
4. 保持原文风格

原文：{full_content}

输出 JSON：
{{"optimized_content": "...", "changes": [{"type": "value_add", "detail": "..."}]}}
""",

    "fact_correct": """你是一个专业的公众号文章事实修正专家。
请根据评估中发现的事实风险点修正以下文章：
1. 检查并修正可能夸大的表达
2. 修正可能不准确的描述
3. 为缺乏依据的断言增加限定语
4. 保持文章核心观点和价值
5. 如果无法确定事实准确性，增加"据我们了解"等限定

原文：{full_content}
评估问题：{issues}

输出 JSON：
{{"optimized_content": "...", "corrections": [{"original": "...", "corrected": "..."}]}}
""",

    "full_rewrite": """你是一个专业的公众号文章写作专家。
请对以下文章进行全文重写：
1. 保留所有核心事实、数据和观点
2. 完全重新组织语言和结构
3. 提升文章的整体质量、可读性和价值
4. 保持公众号文章的风格调性
5. 不得增加虚构的内容

原文：{full_content}

输出 JSON：
{{"optimized_content": "...", "change_summary": "全文重写：..."}}
""",

    "style_transform": """你是一个专业的公众号文章风格转换专家。
请按指定的风格重写以下文章：
1. 保留所有核心事实和观点
2. 按目标风格重新组织语言
3. 调整语气和表达方式

原文：{full_content}
目标风格：{instruction}

输出 JSON：
{{"optimized_content": "...", "style_used": "{instruction}", "change_summary": "..."}}
""",
}

# 高风险内容关键词
HIGH_RISK_KEYWORDS = [
    "医疗", "治疗", "药", "诊断", "手术",
    "法律", "律师", "诉讼", "赔偿",
    "投资", "理财", "收益率", "保本",
    "政治", "政策", "政府", "监管",
]


class ArticleOptimizationService:
    """文章优化稿生成服务"""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        return self._client

    def _check_high_risk(self, article: Article) -> bool:
        """检查文章是否包含高风险内容"""
        text = f"{article.main_title or ''} {article.full_content or article.content or ''}"
        for kw in HIGH_RISK_KEYWORDS:
            if kw in text:
                return True
        return False

    def _check_optimization_limit(self, db: Session, article_id: int) -> bool:
        """检查是否达到最大优化次数限制"""
        count = (
            db.query(ArticleOptimization)
            .filter(
                ArticleOptimization.source_article_id == article_id,
                ArticleOptimization.status.in_(["approved", "published"]),
            )
            .count()
        )
        return count >= 3  # 最多 3 次自动优化

    def _extract_opening(self, text: str, length: int = 500) -> str:
        """提取正文开头"""
        clean = re.sub(r"<[^>]+>", "", text or "")
        return clean[:length]

    def _extract_rest(self, text: str, opening_len: int = 500) -> str:
        """提取开头之后的内容"""
        clean = re.sub(r"<[^>]+>", "", text or "")
        return clean[opening_len:][:3000]

    def _truncate(self, text: str, max_len: int = 4000) -> str:
        """截断长文本"""
        clean = re.sub(r"<[^>]+>", "", text or "")
        if len(clean) > max_len:
            return clean[:max_len] + "\n\n[...]"
        return clean

    def _build_prompt(self, opt_type: str, article: Article,
                      instruction: str = "", evaluation=None) -> str:
        """按优化类型构建 Prompt"""
        prompt_tpl = PROMPTS.get(opt_type)
        if not prompt_tpl:
            raise ValueError(f"Unknown optimization type: {opt_type}")

        title = article.main_title or article.topic or ""
        full_content = article.full_content or article.content or ""
        opening = self._extract_opening(full_content)
        rest = self._extract_rest(full_content)

        issues = ""
        if evaluation and evaluation.issues:
            issues = json.dumps(evaluation.issues, ensure_ascii=False)

        return prompt_tpl.format(
            title=title,
            opening=opening,
            rest=rest,
            full_content=self._truncate(full_content),
            issues=issues,
            instruction=instruction or "通俗易懂",
        )

    async def generate(self, db: Session, article: Article,
                       opt_type: str, instruction: str = "",
                       evaluation_id: int = None) -> dict:
        """生成优化稿

        Args:
            db: 数据库会话
            article: 源文章
            opt_type: 优化类型
            instruction: 额外指令（用于 style_transform 等）
            evaluation_id: 关联的评分记录 ID

        Returns:
            包含优化结果的 dict
        """
        # 检查高风险内容
        is_high_risk = self._check_high_risk(article)

        # 检查优化限制
        if self._check_optimization_limit(db, article.id):
            raise ValueError(f"Article {article.id} has reached max optimization count")

        # 获取关联的评分记录
        evaluation = None
        if evaluation_id:
            evaluation = db.query(ArticleQualityEvaluation).filter(
                ArticleQualityEvaluation.id == evaluation_id
            ).first()

        # 计算优化代数
        gen_count = (
            db.query(ArticleOptimization)
            .filter(ArticleOptimization.source_article_id == article.id)
            .count()
        )
        optimization_generation = gen_count + 1

        # 构建 Prompt 并调用 LLM
        prompt = self._build_prompt(opt_type, article, instruction, evaluation)

        try:
            client = self._get_client()
            resp = await client.chat.completions.create(
                model=settings.dashscope_model or "qwen-plus",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc

        # 解析结果
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"optimized_content": raw, "change_summary": "Raw text output"}

        optimized_body = (
            result.get("optimized_content") or
            result.get("optimized_opening") or
            raw
        )

        # 创建优化版 Article
        opt_article = Article(
            tenant_id=article.tenant_id,
            task_id=f"opt_{article.id}_{opt_type}_{optimization_generation}",
            user_id=article.user_id,
            topic=article.topic,
            style=article.style,
            main_title=result.get("recommended") or result.get("optimized_title") or article.main_title,
            content=optimized_body,
            full_content=optimized_body,
            status="pending_review",
            phase="OPTIMIZATION_DRAFT",
            source_article_id=article.id,
            optimization_generation=optimization_generation,
            optimization_status="draft_ready",
            manual_optimization_disabled=is_high_risk,
        )
        db.add(opt_article)
        db.flush()

        # 创建优化记录
        opt_record = ArticleOptimization(
            tenant_id=article.tenant_id,
            source_article_id=article.id,
            optimized_article_id=opt_article.id,
            trigger_type="auto" if not evaluation_id else "manual",
            trigger_evaluation_id=evaluation_id,
            optimization_type=opt_type,
            optimization_generation=optimization_generation,
            optimization_instruction=instruction,
            model_name=settings.dashscope_model or "qwen-plus",
            prompt_version=PROMPT_VERSION,
            change_summary=result.get("change_summary") or result.get("reason", ""),
            status="draft_ready",
        )
        db.add(opt_record)
        db.commit()

        # 触发优化稿质量复评
        try:
            from app.tasks.quality_tasks import evaluate_article_quality
            evaluate_article_quality.delay(opt_article.id)
        except Exception as exc:
            logger.warning("Failed to trigger quality re-evaluation: %s", exc)

        return {
            "optimization_id": opt_record.id,
            "article_id": opt_article.id,
            "type": opt_type,
            "status": "draft_ready",
        }


optimization_service = ArticleOptimizationService()
