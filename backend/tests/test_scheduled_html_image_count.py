"""定时 HTML 仿写图片数量配置的契约与槽位路由测试。"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.scheduled_tasks import ScheduledTaskCreate, ScheduledTaskUpdate
from app.schemas.article import ArticleState, ImageRequirement, SelectedTitle


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件是纯契约/单元测试，不连接或清理项目业务数据库。"""

    yield


def test_scheduled_task_image_count_defaults_to_five_and_accepts_nineteen() -> None:
    """旧任务默认保留五张，测试任务可以明确配置为十九张。"""

    default_request = ScheduledTaskCreate(name="HTML 默认任务", publish_times=["08:00"])
    configured_request = ScheduledTaskCreate(
        name="HTML 19 图任务",
        publish_times=["08:00"],
        html_image_count=19,
    )

    assert default_request.html_image_count == 5
    assert configured_request.html_image_count == 19
    assert ArticleState(task_id="test", topic="测试").max_generated_images == 5


def test_scheduled_task_layout_mode_defaults_to_standard_and_requires_explicit_poster_mode() -> None:
    """历史定时任务必须默认使用旧版式，海报模式只能由任务显式开启。"""

    default_request = ScheduledTaskCreate(name="绣蔓仿写", publish_times=["08:00"])
    poster_request = ScheduledTaskCreate(
        name="无缝海报测试",
        publish_times=["08:00"],
        layout_mode="seamless_poster",
    )

    assert default_request.layout_mode == "standard"
    assert poster_request.layout_mode == "seamless_poster"

    with pytest.raises(ValidationError):
        ScheduledTaskCreate(
            name="未知版式",
            publish_times=["08:00"],
            layout_mode="auto_detect",
        )


def test_scheduled_task_image_count_rejects_values_outside_one_to_thirty() -> None:
    """图片数量必须有成本上限，避免错误配置一次生成过多图片。"""

    with pytest.raises(ValidationError):
        ScheduledTaskCreate(name="过小", publish_times=["08:00"], html_image_count=0)
    with pytest.raises(ValidationError):
        ScheduledTaskUpdate(html_image_count=31)


def test_html_agent_uses_state_image_count_for_slot_selection(monkeypatch) -> None:
    """HTML Agent 应将任务配置传给槽位选择器，而不是继续使用全局硬编码。"""

    import app.services.article_agent_service as article_agent_service
    import app.services.html_imitation_service as html_imitation_service

    captured = {}

    def fake_select_html_image_slots(blueprint, **kwargs):
        captured["max_generated_images"] = kwargs["max_generated_images"]
        return (tuple(slot.slot_id for slot in blueprint.image_slots), tuple())

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        return '{"text_slots": [{"id": "text-1", "content": "创新正文"}], "image_slots": []}'

    monkeypatch.setattr(
        html_imitation_service,
        "select_html_image_slots",
        fake_select_html_image_slots,
    )
    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)

    state = ArticleState(
        task_id="html-count-test",
        topic="测试主题",
        title=SelectedTitle(main_title="新标题", sub_title=""),
        reference_html='<section><p>原始标题</p><img src="https://example.com/a.jpg"></section>',
        max_generated_images=19,
        skip_reference_image_understanding=True,
    )

    result = asyncio.run(article_agent_service.agent3_generate_html_imitation_content(state))

    assert result.error is None
    assert captured["max_generated_images"] == 19


def test_image_agent_uses_state_image_count_instead_of_global_five_image_cap() -> None:
    """图片 Agent 必须继续尊重 HTML 任务的十九图配置，旧入口仍由默认值保护。"""

    from app.services.article_agent_service import agent5_generate_images

    requirements = [
        ImageRequirement(
            position=index,
            type="inline",
            image_source="DASHSCOPE",
            keywords=f"图片{index}",
            prompt=f"图片{index}提示词",
            placeholder_id=f"image-{index}",
        )
        for index in range(1, 20)
    ]
    state = ArticleState(
        task_id="html-count-agent5-test",
        topic="测试主题",
        max_generated_images=19,
        image_requirements=requirements,
        selected_image_urls=[f"https://example.com/generated-{index}.jpg" for index in range(1, 20)],
    )

    result = asyncio.run(agent5_generate_images(state))

    assert len(result.image_requirements) == 19
    assert len(result.images) == 19


def test_scheduled_task_ui_exposes_html_image_count() -> None:
    """定时任务编辑页必须包含默认值、编辑回填和 1-30 数字控件。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "views"
        / "ScheduledTasksView.vue"
    ).read_text(encoding="utf-8")

    assert "html_image_count" in source
    assert "html_image_count: 5" in source
    assert ":min=\"1\"" in source
    assert ":max=\"30\"" in source


def test_scheduled_task_ui_exposes_explicit_poster_layout_mode() -> None:
    """前端必须显示版式模式，并把旧任务默认值固定为 standard。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "views"
        / "ScheduledTasksView.vue"
    ).read_text(encoding="utf-8")

    assert "layout_mode: 'standard'" in source
    assert "value=\"standard\"" in source
    assert "value=\"seamless_poster\"" in source
