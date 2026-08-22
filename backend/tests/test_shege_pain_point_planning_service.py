"""她格经营痛点策划服务的纯单元测试。"""

from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具，使本模块始终保持纯函数测试。"""

    yield


def test_plan_shege_pain_point_skips_recently_used_topics() -> None:
    """近期已经发布的痛点不能再次被安排，避免公众号连续同质化。"""

    from app.services.shege_pain_point_planning_service import plan_shege_pain_point

    plan = plan_shege_pain_point(
        recent_topics=("客户线索跟进断层", "报价依赖个人经验"),
        now=datetime(2026, 8, 17, 13, 0),
    )

    assert plan.topic not in {"客户线索跟进断层", "报价依赖个人经验"}
    assert "一个具体经营痛点" in plan.constraints[0]
    assert "现象" in plan.constraints[1]
    assert "复盘" in plan.constraints[1]


def test_plan_shege_pain_point_is_stable_for_the_same_slot() -> None:
    """同一时段重试必须复用同一痛点，不能因重试改写已生成文章方向。"""

    from app.services.shege_pain_point_planning_service import plan_shege_pain_point

    now = datetime(2026, 8, 17, 8, 0)
    first = plan_shege_pain_point(recent_topics=(), now=now)
    retried = plan_shege_pain_point(recent_topics=(), now=now)

    assert first == retried


def test_plan_shege_pain_point_reuses_a_frozen_topic_after_retry() -> None:
    """运行记录已经冻结选题时，后续历史变化也不能让重试换题。"""

    from app.services.shege_pain_point_planning_service import plan_shege_pain_point

    plan = plan_shege_pain_point(
        recent_topics=("客户线索跟进断层",),
        now=datetime(2026, 8, 17, 8, 0),
        frozen_topic="报价依赖个人经验：同一份需求，为何不同销售给出完全不同的判断",
    )

    assert plan.topic.startswith("报价依赖个人经验")
    assert "报价依赖个人经验" in plan.constraints[0]


def test_shege_constraints_tell_the_html_agent_which_topics_to_avoid() -> None:
    """HTML 正文生成必须收到近期主题排除与深度拆解规则。"""

    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_agent_service import _build_html_imitation_prompt
    from app.services.shege_pain_point_planning_service import plan_shege_pain_point

    plan = plan_shege_pain_point(
        recent_topics=("会议很多但决策不落地",),
        now=datetime.now(),
    )
    state = ArticleState(
        task_id="shege-pain-point-test",
        topic=plan.topic,
        style="shege_enterprise_ai_service",
        title=SelectedTitle(main_title="测试标题", sub_title=""),
        content_constraints=list(plan.constraints),
    )

    prompt = _build_html_imitation_prompt(state, {}, {})

    assert plan.topic in prompt
    assert "会议很多但决策不落地" in prompt
    assert "一个具体经营痛点" in prompt
