"""仿写任务 HTML 版式模式的行为测试。

这些测试聚焦任务模式的公共契约与生成路由，不重复验证 DOM 槽位算法；DOM 的
解析、回填和图片原位替换已由 ``test_html_imitation_service.py`` 独立覆盖。
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.imitation import TaskCreate
from app.schemas.article import TitleOption
from app.services.imitation_service import (
    create_imitation_task,
    execute_imitation_generation,
    select_sources_for_task,
)


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库清理夹具；本文件全部是无数据库纯单元测试。"""

    yield


class _RecordingDb:
    """记录新增模型的最小数据库替身，避免任务创建测试连接真实 MySQL。"""

    def __init__(self) -> None:
        self.added = None

    def add(self, model) -> None:
        self.added = model

    def commit(self) -> None:
        return None

    def refresh(self, model) -> None:
        model.id = 1


class _SelectionQuery:
    """记录来源选择 SQL 条件，并返回一组最小 ORM 替身。"""

    def __init__(self, rows=None, first_row=None) -> None:
        self.rows = rows or []
        self.first_row = first_row
        self.filters = []

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def order_by(self, *args):
        return self

    def limit(self, count):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.first_row


class _SourceSelectionDb:
    """按查询模型返回固定结果，隔离真实数据库并暴露文章筛选条件。"""

    def __init__(self) -> None:
        source = SimpleNamespace(
            id=1,
            pool_id=2,
            feed_source_id=3,
            weight=1,
            wechat_name="参考公众号",
        )
        self.source_query = _SelectionQuery(rows=[source])
        self.feed_query = _SelectionQuery(first_row=SimpleNamespace(name="参考源"))
        self.article_query = _SelectionQuery(rows=[])

    def query(self, model):
        if model.__name__ == "ImitationPoolSource":
            return self.source_query
        if model.__name__ == "FeedSource":
            return self.feed_query
        if model.__name__ == "FeedSourceArticle":
            return self.article_query
        raise AssertionError(f"未预期的查询模型: {model.__name__}")


def _task(imitation_mode: str) -> SimpleNamespace:
    """构造执行服务需要的最小任务对象。"""

    return SimpleNamespace(
        id=7,
        tenant_id=3,
        created_by=9,
        name="每日家居仿写",
        imitation_mode=imitation_mode,
        knowledge_base_ids=None,
        footer_template="",
    )


def _source(*, body_html: str = "") -> dict:
    """构造包含 Markdown 与可选原始 HTML 的投喂文章。"""

    return {
        "articles": [
            {
                "id": 11,
                "title": "参考文章",
                "body_markdown": "",
                "body_html": body_html,
            }
        ]
    }


def test_task_create_defaults_to_content_mode() -> None:
    """旧客户端未传模式时必须继续使用现有内容结构仿写。"""

    request = TaskCreate(name="每日仿写", pool_id=1)

    assert request.imitation_mode == "content"


def test_task_create_accepts_html_layout_and_rejects_unknown_mode() -> None:
    """API 只接受两个明确模式，防止拼写错误静默改变生成行为。"""

    request = TaskCreate(name="每日仿写", pool_id=1, imitation_mode="html_layout")

    assert request.imitation_mode == "html_layout"
    with pytest.raises(ValidationError):
        TaskCreate(name="每日仿写", pool_id=1, imitation_mode="unknown")


def test_create_imitation_task_persists_requested_mode() -> None:
    """服务层必须把 API 选择的模式写入任务模型。"""

    db = _RecordingDb()

    task = create_imitation_task(
        db,
        tenant_id=3,
        name="HTML 版式任务",
        pool_id=2,
        imitation_mode="html_layout",
    )

    assert db.added is task
    assert task.imitation_mode == "html_layout"


def test_migration_script_can_load_outside_backend_working_directory(tmp_path) -> None:
    """部署脚本应自行定位 backend，不能依赖操作者预先配置 PYTHONPATH。"""

    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "migrate_imitation_html_layout.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(script_path)!r}, run_name='migration_import_test')"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_html_source_selection_excludes_empty_and_whitespace_html() -> None:
    """HTML 模式的 SQL 必须排除空串和纯空白，避免跳过后续可用文章。"""

    db = _SourceSelectionDb()
    task = SimpleNamespace(pool_id=2, strategy="round_robin", imitation_mode="html_layout")

    select_sources_for_task(db, task, count=1)

    filter_sql = " ".join(str(condition) for condition in db.article_query.filters).lower()
    assert "trim(feed_source_articles.body_html)" in filter_sql


def _patch_generation_agents(monkeypatch, captured_states: list) -> None:
    """替换外部模型与图片调用，只观察服务传给 Agent 的 ArticleState。"""

    import app.services.article_agent_service as agent_service

    async def fake_title(state):
        state.title_options = [TitleOption(main_title="新标题", sub_title="新副标题")]
        return state

    async def fake_outline(state):
        return state

    async def fake_content(state):
        captured_states.append(state.model_copy(deep=True))
        state.content = "<section><p>新正文</p></section>"
        return state

    async def fake_images(state):
        return state

    def fake_merge(state):
        state.full_content = state.content
        return state

    monkeypatch.setattr(agent_service, "agent1_generate_title_options", fake_title)
    monkeypatch.setattr(agent_service, "agent2_generate_outline", fake_outline)
    monkeypatch.setattr(agent_service, "agent3_generate_content", fake_content)
    monkeypatch.setattr(agent_service, "agent4_analyze_image_requirements", fake_images)
    monkeypatch.setattr(agent_service, "agent5_generate_images", fake_images)
    monkeypatch.setattr(agent_service, "merge_images_into_content", fake_merge)


@pytest.mark.asyncio
async def test_html_layout_mode_passes_reference_html_to_content_agent(monkeypatch) -> None:
    """HTML 模式必须触发现有 ``reference_html`` 槽位生成分支。"""

    captured_states = []
    _patch_generation_agents(monkeypatch, captured_states)
    reference_html = '<section style="border:1px solid #333"><p>原文</p></section>'

    result = await execute_imitation_generation(
        _RecordingDb(),
        _task("html_layout"),
        _source(body_html=reference_html),
        slot_index=0,
    )

    assert result["success"] is True
    assert captured_states[0].reference_html == reference_html


@pytest.mark.asyncio
async def test_content_mode_does_not_enable_html_layout(monkeypatch) -> None:
    """旧模式即使来源已有 HTML，也不能改变历史任务的输出路径。"""

    captured_states = []
    _patch_generation_agents(monkeypatch, captured_states)

    result = await execute_imitation_generation(
        _RecordingDb(),
        _task("content"),
        _source(body_html="<section><p>原文</p></section>"),
        slot_index=0,
    )

    assert result["success"] is True
    assert captured_states[0].reference_html is None


@pytest.mark.asyncio
async def test_html_layout_mode_fails_clearly_when_source_has_no_html(monkeypatch) -> None:
    """缺失 HTML 时不得偷偷回退到 Markdown，避免用户误判版式已复刻。"""

    captured_states = []
    _patch_generation_agents(monkeypatch, captured_states)

    result = await execute_imitation_generation(
        _RecordingDb(),
        _task("html_layout"),
        _source(body_html=""),
        slot_index=0,
    )

    assert result["success"] is False
    assert "原始HTML" in result["error"]
    assert captured_states == []


@pytest.mark.asyncio
async def test_html_layout_mode_propagates_content_agent_failure(monkeypatch) -> None:
    """正文 Agent 报错或未产出正文时，任务不能被计为成功生成。"""

    captured_states = []
    _patch_generation_agents(monkeypatch, captured_states)
    import app.services.article_agent_service as agent_service

    async def failing_content_agent(state):
        state.error = "HTML 槽位内容解析失败"
        state.content = None
        return state

    monkeypatch.setattr(agent_service, "agent3_generate_content", failing_content_agent)

    result = await execute_imitation_generation(
        _RecordingDb(),
        _task("html_layout"),
        _source(body_html="<section><p>原文</p></section>"),
        slot_index=0,
    )

    assert result["success"] is False
    assert "HTML 槽位内容解析失败" in result["error"]
