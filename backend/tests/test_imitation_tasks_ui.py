"""仿写任务页面 HTML 版式模式的前端契约测试。

项目当前没有浏览器单测框架，因此用源码契约覆盖表单默认值、可选项与列表展示；
最终仍由 ``vue-tsc`` 和 Vite 构建验证模板及 TypeScript 的完整正确性。
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具，本文件只读取前端源码。"""

    yield


IMITATION_TASKS_VIEW = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "views"
    / "ImitationTasksView.vue"
)


def test_imitation_task_form_exposes_html_layout_mode() -> None:
    """创建任务时应默认兼容旧模式，并允许显式选择 HTML 版式。"""

    source = IMITATION_TASKS_VIEW.read_text(encoding="utf-8")

    assert "imitation_mode: 'content'" in source
    assert 'v-model="form.imitation_mode"' in source
    assert 'value="content"' in source
    assert 'value="html_layout"' in source
    assert "HTML 版式仿写" in source


def test_imitation_task_list_displays_saved_mode() -> None:
    """任务列表必须显示后端保存的模式，避免用户执行前无法确认生成方式。"""

    source = IMITATION_TASKS_VIEW.read_text(encoding="utf-8")

    assert "imitation_mode: ImitationMode" in source
    assert "imitationModeLabels[row.imitation_mode]" in source


def test_imitation_task_dialog_uses_responsive_width() -> None:
    """创建弹窗在手机视口不能使用固定 600px 宽度裁掉 HTML 模式选项。"""

    source = IMITATION_TASKS_VIEW.read_text(encoding="utf-8")

    assert 'width="min(600px, 94vw)"' in source
