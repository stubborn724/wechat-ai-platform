"""TaGeAI 仿写参数进入队列首个 Agent 的回归测试。

本模块只隔离 URL 抓取、模型调用和标题后的后续 Agent，保留 ContentJob 队列、
TaGeAI 上下文转换及标题 Agent 的真实执行，以防参数仅写入 generation_config
或 ArticleState 却未真正进入模型提示词。
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具，测试全程使用内存替身且不访问 MySQL。"""

    yield


class _QueueQuery:
    """满足队列读取文章槽位所需的最小查询协议。"""

    def filter(self, *args, **kwargs):
        """保持 SQLAlchemy 链式调用形态，队列中没有预创建的槽位。"""

        return self

    def order_by(self, *args, **kwargs):
        """保留排序调用，使测试替身只承担查询边界职责。"""

        return self

    def all(self):
        """返回空槽位列表，让队列走既有的单文章默认分支。"""

        return []

    def scalar(self):
        """取消探针读取到正常生成状态，保留本文件对 Agent 参数传递的关注点。"""

        return "generating"

    def first(self):
        """队列参数测试没有历史版本，首次生成应从第 1 版开始。"""

        return None


class _QueueDb:
    """隔离持久化副作用的队列数据库替身。"""

    def __init__(self):
        """记录写入对象，便于必要时扩展对失败版本的断言。"""

        self.added = []

    def query(self, *args, **kwargs):
        """返回满足槽位读取需求的查询替身。"""

        return _QueueQuery()

    def add(self, value):
        """模拟 ORM 暂存写入，不建立外部数据库连接。"""

        self.added.append(value)

    def flush(self):
        """队列测试不依赖数据库生成的主键，因此无需额外处理。"""

        return None

    def commit(self):
        """保留事务提交边界，避免测试替身改变生产控制流。"""

        return None


@pytest.mark.parametrize(
    ("reference", "expected_reference"),
    [
        (
            {"type": "text", "value": "短参考正文：久坐腰背支撑建议。"},
            "短参考正文：久坐腰背支撑建议。",
        ),
        (
            {"type": "url", "value": "https://example.com/short-reference"},
            "URL 短参考正文：久坐腰背支撑建议。",
        ),
    ],
)
def test_tageai_imitate_reference_reaches_title_agent_prompt_from_queue(
    monkeypatch,
    reference,
    expected_reference,
):
    """文本和 URL 仿写参考均须穿过队列并进入真实标题 Agent 提示词。"""

    from app.services import article_agent_service, feed_service
    from app.services.job_queue_service import process_job_batch

    captured_prompts = []

    async def fake_title_model(system_message, prompt, **kwargs):
        """替换外部模型，仅保存真实标题 Agent 已组装完成的提示词。"""

        captured_prompts.append(prompt)
        return '{"title_options":[{"main_title":"测试标题","sub_title":"测试副标题"}]}'

    async def fake_fetch(url):
        """模拟已通过现有抓取服务规范化的 URL 正文，不访问网络。"""

        assert url == reference["value"]
        return {"body_markdown": expected_reference}

    async def stop_after_title(state):
        """在标题 Agent 后终止，避免测试触发大纲、正文和图片外部依赖。"""

        raise RuntimeError("test-stop-after-title-agent")

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_title_model)
    monkeypatch.setattr(article_agent_service, "agent2_generate_outline", stop_after_title)
    if reference["type"] == "url":
        monkeypatch.setattr(feed_service, "_fetch_single_url", fake_fetch)

    job = SimpleNamespace(
        id=803,
        tenant_id=7,
        created_by=11,
        topic="人体工学椅",
        footer_template=None,
        status="queued",
        generation_config={
            "article_count": 1,
            "tageai_operation": "imitate",
            "tageai_reference": reference,
            "content_constraints": ["不得虚构产品参数"],
        },
    )

    process_job_batch(_QueueDb(), job)

    assert len(captured_prompts) == 1
    assert expected_reference in captured_prompts[0]
    assert "不得虚构产品参数" in captured_prompts[0]


def test_tageai_title_override_reaches_outline_from_queue_without_model_rewrite(monkeypatch):
    """指定标题必须在真实队列中成为后续 Agent 的标题，不能被模型重新生成。"""

    from app.services import article_agent_service
    from app.services.job_queue_service import process_job_batch

    captured_titles = []

    async def unexpected_title_model(*args, **kwargs):
        """标题覆盖存在时，模型调用代表业务语义被破坏，因此测试直接失败。"""

        raise AssertionError("titleOverride 不应调用标题模型")

    async def capture_outline(state):
        """捕获队列已经写入 SelectedTitle 的状态后停止后续外部依赖。"""

        captured_titles.append(state.title.main_title)
        raise RuntimeError("test-stop-after-title-override")

    monkeypatch.setattr(article_agent_service, "_call_llm", unexpected_title_model)
    monkeypatch.setattr(article_agent_service, "agent2_generate_outline", capture_outline)
    job = SimpleNamespace(
        id=804,
        tenant_id=7,
        created_by=11,
        topic="人体工学椅",
        footer_template=None,
        status="queued",
        generation_config={
            "article_count": 1,
            "tageai_operation": "generate",
            "title_override": "久坐人群如何选择人体工学椅",
        },
    )

    process_job_batch(_QueueDb(), job)

    assert captured_titles == ["久坐人群如何选择人体工学椅"]


def test_tageai_imitate_title_override_reference_and_constraints_reach_outline_prompt(monkeypatch):
    """仿写任务的指定标题、参考和约束必须同时进入真实大纲 Agent。

    ``titleOverride`` 会让标题 Agent 跳过模型调用，容易造成后续大纲遗漏参考或约束。
    本测试从 ContentJob 队列驱动到真实 ``agent2_generate_outline`` 的提示词边界，
    锁定三类受控输入在同一次仿写调用中的组合语义。
    """

    from app.services import article_agent_service
    from app.services.job_queue_service import process_job_batch

    captured_prompts = []

    async def unexpected_title_model(*args, **kwargs):
        """指定标题存在时不应调用标题模型，避免模型改写调用方明确指定的标题。"""

        raise AssertionError("imitate + titleOverride 不应调用标题模型")

    async def fake_outline_model(system_message, prompt, handler, **kwargs):
        """保存真实大纲 Agent 组装后的提示词，并返回最小有效大纲。"""

        captured_prompts.append(prompt)
        return '{"sections":[{"section":1,"title":"开场","points":["久坐痛点"]}]}'

    async def stop_after_outline(state):
        """大纲已验证后终止，避免正文、图片等后续 Agent 访问外部依赖。"""

        raise RuntimeError("test-stop-after-outline-agent")

    monkeypatch.setattr(article_agent_service, "_call_llm", unexpected_title_model)
    monkeypatch.setattr(article_agent_service, "_call_llm_with_streaming", fake_outline_model)
    monkeypatch.setattr(article_agent_service, "agent3_generate_content", stop_after_outline)

    title_override = "久坐人群的腰背支撑指南"
    reference_text = "参考文章正文：先讲久坐痛点，再讲腰背支撑方案。"
    constraints = ["不得虚构产品参数", "结尾必须给出选购建议"]
    job = SimpleNamespace(
        id=805,
        tenant_id=7,
        created_by=11,
        topic="人体工学椅",
        footer_template=None,
        status="queued",
        generation_config={
            "article_count": 1,
            "tageai_operation": "imitate",
            "tageai_reference": {"type": "text", "value": reference_text},
            "title_override": title_override,
            "content_constraints": constraints,
        },
    )

    process_job_batch(_QueueDb(), job)

    assert len(captured_prompts) == 1
    outline_prompt = captured_prompts[0]
    assert title_override in outline_prompt
    assert reference_text in outline_prompt
    for constraint in constraints:
        assert constraint in outline_prompt
