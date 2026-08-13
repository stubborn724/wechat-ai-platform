"""TaGeAI 仿写上下文进入真实内容队列的回归测试。

本文件只驱动本地 Python 代码，不访问 MySQL、模型服务或微信公众号。测试刻意在
``process_job_batch`` 的首个 Agent 边界捕获 ``ArticleState``，避免只验证配置落库而
遗漏队列消费者这一真实链路。
"""

import asyncio
import io
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件使用内存替身，不加载全局 MySQL 清理夹具。"""

    yield


class _FakeQuery:
    """满足队列读取槽位所需的最小查询接口。"""

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def scalar(self):
        """取消探针查询的状态为空，表示测试任务没有收到取消请求。"""

        return None

    def first(self):
        """默认没有历史版本，保持既有单次生成测试的初始编号语义。"""

        return None


class _FakeQueueDb:
    """只记录队列流水线写入对象，不执行任何数据库操作。"""

    def __init__(self):
        self.added = []
        self.commit_count = 0

    def query(self, *args, **kwargs):
        return _FakeQuery()

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commit_count += 1


class _GbkOutput(io.StringIO):
    """模拟 Windows GBK 控制台，写入不可编码字符时与真实输出流一样失败。"""

    @property
    def encoding(self):
        """显式声明编码，供生产代码决定是否需要降级。"""

        return "gbk"

    def write(self, text):
        """先执行 GBK 编码校验，避免测试替身错误地接受任意 Unicode。"""

        text.encode(self.encoding)
        return super().write(text)


def test_agent_progress_output_degrades_characters_unsupported_by_gbk(monkeypatch):
    """进度日志包含特殊符号时，GBK 控制台不能中断文章生成。"""

    from app.services import article_agent_service

    output = _GbkOutput()
    monkeypatch.setattr(article_agent_service.sys, "stdout", output)

    article_agent_service.emit_progress_message("  ▶ agent1: 生成标题方案...")

    assert "agent1" in output.getvalue()


def test_title_agent_accepts_a_single_title_option_object(monkeypatch):
    """兼容 OpenAI 网关将单元素数组压缩为对象的合法 JSON 响应。"""

    from app.schemas.article import ArticleState
    from app.services import article_agent_service

    async def single_option_response(*args, **kwargs):
        return '{"title_options":{"main_title":"人体工学椅选购指南","sub_title":"找到适合久坐的支撑方案"}}'

    monkeypatch.setattr(article_agent_service, "_call_llm", single_option_response)

    result = asyncio.run(
        article_agent_service.agent1_generate_title_options(
            ArticleState(task_id="single-title-option", topic="人体工学椅")
        )
    )

    assert [(item.main_title, item.sub_title) for item in result.title_options] == [
        ("人体工学椅选购指南", "找到适合久坐的支撑方案"),
    ]


def test_title_agent_uses_topic_when_model_returns_an_empty_option_list(monkeypatch):
    """上游返回格式正确但无候选时，生成任务仍要有明确且可追溯的标题。"""

    from app.schemas.article import ArticleState
    from app.services import article_agent_service

    async def empty_option_response(*args, **kwargs):
        return '{"title_options":[]}'

    monkeypatch.setattr(article_agent_service, "_call_llm", empty_option_response)

    result = asyncio.run(
        article_agent_service.agent1_generate_title_options(
            ArticleState(task_id="empty-title-option", topic="智能家居的未来趋势")
        )
    )

    assert [(item.main_title, item.sub_title) for item in result.title_options] == [
        ("智能家居的未来趋势", ""),
    ]
    assert result.error is None


def test_tageai_delivery_rejects_versions_without_generated_body():
    """空正文版本不能进入草稿投递或自动批准流程。"""

    from app.tasks.job_tasks import ContentGenerationFailed, require_deliverable_versions

    empty_version = SimpleNamespace(body_markdown="   ")

    with pytest.raises(ContentGenerationFailed, match="未得到可保存的正文"):
        require_deliverable_versions([empty_version])


def test_next_content_version_number_follows_existing_versions():
    """同一内容任务恢复时必须写入下一版本，不能碰撞历史失败版本。"""

    from app.services.job_queue_service import next_content_version_number

    class _VersionQuery:
        """固定返回当前最高版本，隔离 SQLAlchemy 查询细节。"""

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(version_number=3)

    class _VersionDb:
        """只实现版本号读取所需的数据库接口。"""

        def query(self, *args, **kwargs):
            return _VersionQuery()

    assert next_content_version_number(_VersionDb(), SimpleNamespace(id=807, tenant_id=7)) == 4


def test_generation_progress_snapshot_is_persisted_with_a_heartbeat():
    """Worker 每个长耗时阶段都要提交公开进度，Gateway 重查时不能只得到固定生成中。"""

    from app.services.job_queue_service import record_tageai_generation_progress

    job = SimpleNamespace(generation_config={"generation_budget": {"image_count": 5}})
    db = _FakeQueueDb()

    record_tageai_generation_progress(
        db,
        job,
        stage="MEDIA_GENERATING",
        text_progress=100,
        media_total=5,
        media_ready=3,
        media_generating=2,
    )

    snapshot = job.generation_config["progress_snapshot"]
    assert snapshot["platform"] == "wechat"
    assert snapshot["stage"] == "MEDIA_GENERATING"
    assert snapshot["media_ready"] == 3
    assert snapshot["media_generating"] == 2
    assert snapshot["heartbeat_at"].endswith("+00:00")
    assert db.commit_count == 1


def test_explicit_zero_image_budget_is_preserved_for_the_queue_consumer():
    """用户要求不生成图片时，队列不能把零误判为缺省值并回退到五张。"""

    from app.services.job_queue_service import resolve_image_generation_limit

    assert resolve_image_generation_limit({"generation_budget": {"image_count": 0}}) == 0


@pytest.mark.asyncio
async def test_image_agent_skips_all_generation_when_the_frozen_budget_is_zero():
    """零图片预算是明确约束，任何直接调用图片 Agent 的入口也必须遵守。"""

    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images

    state = ArticleState(
        task_id="zero-image-budget",
        topic="科技文章",
        max_generated_images=0,
        image_requirements=[
            ImageRequirement(
                position=1,
                type="inline",
                image_source="DASHSCOPE",
                keywords="人工智能",
            )
        ],
    )

    result = await agent5_generate_images(state)

    assert result.images == []
    assert result.image_requirements == []


def test_worker_marks_empty_generated_content_as_failed_without_delivery(monkeypatch):
    """Worker 发现空正文后必须失败关闭，不能进入草稿创建和自动批准。"""

    from app.tasks import job_tasks

    class _TaskQuery:
        """为 Worker 的当前任务读取提供最小链式查询实现。"""

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return job

    class _TaskDb:
        """记录失败收敛事务，避免测试访问真实数据库或 Celery。"""

        def __init__(self):
            self.commit_count = 0
            self.rollback_count = 0

        def query(self, *args, **kwargs):
            return _TaskQuery()

        def commit(self):
            self.commit_count += 1

        def rollback(self):
            self.rollback_count += 1

        def close(self):
            return None

    job = SimpleNamespace(id=806, status="queued", content_type="article", error_code=None, error_message=None)
    db = _TaskDb()
    delivery_called = False

    def unexpected_delivery(*args, **kwargs):
        nonlocal delivery_called
        delivery_called = True

    monkeypatch.setattr(job_tasks, "MysqlSessionLocal", lambda: db)
    monkeypatch.setattr(job_tasks, "claim_dispatched_job_for_execution", lambda *args: job)
    monkeypatch.setattr(job_tasks, "process_job_batch", lambda *args: [SimpleNamespace(body_markdown="")])
    monkeypatch.setattr(job_tasks, "_save_versions_as_articles_and_drafts", unexpected_delivery)

    result = job_tasks.process_content_job.run(job.id)

    assert result == {
        "job_id": job.id,
        "status": "failed",
        "error_code": "CONTENT_GENERATION_FAILED",
    }
    assert job.status == "failed"
    assert job.error_code == "CONTENT_GENERATION_FAILED"
    assert delivery_called is False


def test_tageai_imitate_inputs_are_hydrated_before_queue_calls_first_agent(monkeypatch):
    """仿写参考、指定标题和内容约束必须进入队列生成状态。"""

    from app.services import article_agent_service
    from app.services.job_queue_service import process_job_batch

    observed_states = []

    async def stop_after_capture(state):
        """在外部模型调用前保存状态，保证测试没有网络副作用。"""

        observed_states.append(state)
        raise RuntimeError("test-stop-before-llm")

    monkeypatch.setattr(article_agent_service, "agent1_generate_title_options", stop_after_capture)
    job = SimpleNamespace(
        id=801,
        tenant_id=7,
        created_by=11,
        topic="新一代人体工学椅",
        footer_template=None,
        status="queued",
        generation_config={
            "article_count": 1,
            "tageai_operation": "imitate",
            "tageai_reference": {
                "type": "text",
                "value": "参考文章正文：先讲久坐痛点，再讲腰背支撑方案。",
            },
            "title_override": "久坐人群的腰背支撑指南",
            "content_constraints": ["不得虚构产品参数", "结尾必须给出选购建议"],
        },
    )

    process_job_batch(_FakeQueueDb(), job)

    assert len(observed_states) == 1
    state = observed_states[0]
    assert state.reference_articles == ["参考文章正文：先讲久坐痛点，再讲腰背支撑方案。"]
    assert getattr(state, "title_override", None) == "久坐人群的腰背支撑指南"
    assert getattr(state, "content_constraints", None) == [
        "不得虚构产品参数",
        "结尾必须给出选购建议",
    ]


def test_tageai_url_reference_is_resolved_before_queue_calls_first_agent(monkeypatch):
    """URL 参考必须先解析为正文，抓取失败时不得静默退化为普通生成。"""

    from app.services import article_agent_service, feed_service
    from app.services.job_queue_service import process_job_batch

    observed_states = []

    async def fake_fetch(url):
        assert url == "https://example.com/reference"
        return {
            "title": "参考公众号文章",
            "body_markdown": "参考文章正文：开场故事、产品场景和收尾互动。",
        }

    async def stop_after_capture(state):
        observed_states.append(state)
        raise RuntimeError("test-stop-before-llm")

    monkeypatch.setattr(feed_service, "_fetch_single_url", fake_fetch)
    monkeypatch.setattr(article_agent_service, "agent1_generate_title_options", stop_after_capture)
    job = SimpleNamespace(
        id=802,
        tenant_id=7,
        created_by=11,
        topic="新一代人体工学椅",
        footer_template=None,
        status="queued",
        generation_config={
            "article_count": 1,
            "tageai_operation": "imitate",
            "tageai_reference": {
                "type": "url",
                "value": "https://example.com/reference",
            },
        },
    )

    process_job_batch(_FakeQueueDb(), job)

    assert len(observed_states) == 1
    assert observed_states[0].reference_articles == [
        "参考文章正文：开场故事、产品场景和收尾互动。"
    ]


def test_tageai_title_override_is_used_without_calling_title_model(monkeypatch):
    """titleOverride 是强制标题，不得被模型候选标题覆盖。"""

    from app.schemas.article import ArticleState
    from app.services import article_agent_service

    state = ArticleState(task_id="tageai-title", topic="人体工学椅")
    # 生产字段尚未加入时也让现有代码可执行，以便红灯准确暴露“未消费”而非测试错误。
    state.__dict__["title_override"] = "久坐人群的腰背支撑指南"
    model_calls = []

    async def fake_title_model(*args, **kwargs):
        model_calls.append((args, kwargs))
        return '{"title_options":[{"main_title":"模型生成标题","sub_title":"模型副标题"}]}'

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_title_model)

    result = asyncio.run(article_agent_service.agent1_generate_title_options(state))

    assert result.title_options[0].main_title == "久坐人群的腰背支撑指南"
    assert model_calls == []


def test_tageai_short_reference_is_injected_into_title_prompt(monkeypatch):
    """短参考正文也必须到达模型提示词，不能因长度阈值被静默丢弃。"""

    from app.schemas.article import ArticleState
    from app.services import article_agent_service

    reference_text = "久坐腰背支撑的三条建议。"
    state = ArticleState(
        task_id="tageai-short-reference",
        topic="人体工学椅",
        reference_articles=[reference_text],
    )
    prompts = []

    async def fake_title_model(system_message, prompt, **kwargs):
        prompts.append(prompt)
        return '{"title_options":[{"main_title":"模型标题","sub_title":"副标题"}]}'

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_title_model)

    asyncio.run(article_agent_service.agent1_generate_title_options(state))

    assert len(prompts) == 1
    assert reference_text in prompts[0]


def test_tageai_content_constraints_are_consumed_by_content_agent(monkeypatch):
    """contentConstraints 必须进入正文 Agent 的真实提示词，不能只保存在 JSON 配置中。"""

    from app.schemas.article import ArticleState, OutlineResult, OutlineSection, SelectedTitle
    from app.services import article_agent_service

    state = ArticleState(
        task_id="tageai-constraints",
        topic="人体工学椅",
        title=SelectedTitle(main_title="标题", sub_title="副标题"),
        outline=OutlineResult(sections=[OutlineSection(section=1, title="开场", points=["痛点"])]),
    )
    state.__dict__["content_constraints"] = ["不得虚构产品参数", "结尾必须给出选购建议"]
    prompts = []

    async def fake_content_model(system_message, prompt, handler, **kwargs):
        prompts.append(prompt)
        return "这是一篇符合约束的正文。"

    monkeypatch.setattr(article_agent_service, "_call_llm_with_streaming", fake_content_model)

    result = asyncio.run(article_agent_service.agent3_generate_content(state))

    assert result.content == "这是一篇符合约束的正文。"
    assert len(prompts) == 1
    assert "不得虚构产品参数" in prompts[0]
    assert "结尾必须给出选购建议" in prompts[0]
