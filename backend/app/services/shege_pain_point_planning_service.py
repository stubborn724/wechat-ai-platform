"""她格原创图文的经营痛点选题与去重服务。

她格内容不能只依赖模型从同一份知识库自由选题，否则容易反复出现“AI 入企、
流程协同、数据复盘”等泛化表达。本模块将可复用的经营痛点显式建模，并在每次
定时执行时结合近期已发布主题选择一个未重复方向，再把深度写作约束注入现有
文章 Agent。模块不负责生成文章，也不修改定时任务，因而可以被手工创建和定时
创建两条链路稳定复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Iterable


SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID = "shege_enterprise_ai_service"
SHEGE_PAIN_POINT_HISTORY_DAYS = 21


@dataclass(frozen=True)
class ShegePainPoint:
    """一个可独立完成深度分析的企业经营问题。"""

    key: str
    topic: str


@dataclass(frozen=True)
class ShegePainPointPlan:
    """本次文章冻结的选题及所有 Agent 共用的写作约束。"""

    topic: str
    constraints: tuple[str, ...]


# 选题按经营链路分布，避免连续三篇都落在“管理会议”或“工具使用”上。每一个
# 条目都足以支撑一篇完整文章，不能只是“效率提升”这类不可验证的抽象词。
SHEGE_PAIN_POINTS: tuple[ShegePainPoint, ...] = (
    ShegePainPoint("客户线索跟进断层", "客户线索跟进断层：为什么销售很忙，意向客户却在关键节点流失"),
    ShegePainPoint("报价依赖个人经验", "报价依赖个人经验：同一份需求，为何不同销售给出完全不同的判断"),
    ShegePainPoint("会议很多但决策不落地", "会议很多但决策不落地：问题不在开会，而在行动没有被追踪"),
    ShegePainPoint("经营数据分散", "经营数据分散：老板每天看很多报表，为何仍然无法及时判断问题"),
    ShegePainPoint("客户需求无法沉淀", "客户需求无法沉淀：一线听到很多反馈，产品和服务为何没有变得更准"),
    ShegePainPoint("跨部门协同断层", "跨部门协同断层：任务交接总靠催，怎样让关键动作看得见、接得住"),
    ShegePainPoint("重复事务挤占时间", "重复事务挤占时间：员工忙于整理信息，真正重要的客户工作被谁挤掉了"),
    ShegePainPoint("新人上手慢", "新人上手慢：经验都在少数人脑中，团队如何缩短岗位学习曲线"),
    ShegePainPoint("复盘只停留在结果", "复盘只停留在结果：看见问题之后，怎样找到下一次能改变的具体动作"),
    ShegePainPoint("试点无法推广", "AI 试点无法推广：一个岗位有效之后，为什么很难变成组织能力"),
    ShegePainPoint("客户服务响应不一致", "客户服务响应不一致：同一个问题反复出现，如何让答复既快又不失真"),
    ShegePainPoint("管理者被信息淹没", "管理者被信息淹没：消息越来越多，哪些信号才真正值得立即处理"),
)


def is_shege_enterprise_ai_style(style: str | None) -> bool:
    """判断任务是否选择她格企业 AI 写作模板。"""

    return str(style or "").strip().lower() == SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID


def plan_shege_pain_point(
    *,
    recent_topics: Iterable[str],
    now: datetime,
    frozen_topic: str | None = None,
) -> ShegePainPointPlan:
    """为一个执行时段选择未在近期使用过的痛点，并生成深度写作约束。

    选择使用时段的稳定哈希而非随机数，保证同一运行记录重试时选题不漂移；近期
    已出现的痛点优先排除，痛点池耗尽时才按稳定顺序回收，避免任务因内容策略而
    停摆。历史标题和主题均可能是完整长句，因此用痛点键做包含匹配。
    """

    normalized_frozen_topic = str(frozen_topic or "").strip()
    frozen_pain_point = next(
        (
            item
            for item in SHEGE_PAIN_POINTS
            if item.key in normalized_frozen_topic
        ),
        None,
    )
    if normalized_frozen_topic and frozen_pain_point is None:
        # 人工主题或未来扩展出的痛点也可以安全重试。没有可识别的标准键时仍保留
        # 已冻结的原主题，并采用相同的深度写作约束。
        frozen_pain_point = ShegePainPoint(
            key=normalized_frozen_topic.split("：", 1)[0],
            topic=normalized_frozen_topic,
        )
    normalized_history = "\n".join(
        str(topic).strip() for topic in recent_topics if str(topic).strip()
    )
    if frozen_pain_point is not None:
        selected = frozen_pain_point
    else:
        available = tuple(
            item for item in SHEGE_PAIN_POINTS if item.key not in normalized_history
        ) or SHEGE_PAIN_POINTS
        slot_key = now.strftime("%Y-%m-%d %H:%M")
        index = int(hashlib.sha256(slot_key.encode("utf-8")).hexdigest(), 16) % len(available)
        selected = available[index]
    excluded = "；".join(
        str(topic).strip() for topic in recent_topics if str(topic).strip()
    ) or "无"
    return ShegePainPointPlan(
        topic=selected.topic,
        constraints=(
            f"全文只能围绕“{selected.key}”这一个具体经营痛点展开，禁止扩写成泛泛的 AI 入企介绍或罗列多个问题。",
            "正文必须按“具体业务现象 - 形成根因 - AI 如何嵌入一个真实工作动作 - 分阶段落地步骤 - 用什么指标和复盘动作判断是否有效”完成深度拆解；每一段都服务于同一个痛点。",
            f"近期已使用主题：{excluded}。不得重复这些主题的核心论点、标题方向或案例结构。",
            "不得虚构客户案例、效果数据、报价或技术能力；信息不足时说明适用条件和需要先确认的业务事实。",
        ),
    )


def load_recent_shege_topics(db, *, tenant_id: int, now: datetime) -> tuple[str, ...]:
    """读取近期已完成她格文章主题，供新的运行时段避重。

    只读取已经形成可交付内容的文章，避免失败重试的空记录提前占用选题；查询
    仍限定租户和她格写作模板，其他公众号标题不会污染她格的选题轮换。
    """

    from app.models.mysql_models import Article

    cutoff = now - timedelta(days=SHEGE_PAIN_POINT_HISTORY_DAYS)
    rows = (
        db.query(Article.topic)
        .filter(
            Article.tenant_id == tenant_id,
            Article.style == SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
            Article.topic.isnot(None),
            Article.created_at >= cutoff,
            Article.status.in_(("published", "draft", "publishing")),
        )
        .order_by(Article.created_at.desc())
        .limit(len(SHEGE_PAIN_POINTS) * 3)
        .all()
    )
    return tuple(str(row[0]).strip() for row in rows if str(row[0] or "").strip())
