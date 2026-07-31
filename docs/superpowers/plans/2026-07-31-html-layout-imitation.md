# HTML Layout Imitation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in imitation-task mode that preserves a reference article's HTML layout while the existing Agent fills text and image slots.

**Architecture:** Persist `imitation_mode` on `ImitationTask`, defaulting to the current content-only path. In `html_layout` mode the source selector includes `body_html`, the generation service validates it and assigns it to `ArticleState.reference_html`, then reuses the existing deterministic DOM blueprint and slot-filling pipeline.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, BeautifulSoup, pytest, Vue 3, TypeScript, Element Plus.

---

### Task 1: Persist and expose the imitation mode

**Files:**
- Modify: `backend/app/models/mysql_models.py`
- Modify: `backend/app/api/v1/imitation.py`
- Modify: `backend/app/services/imitation_service.py`
- Create: `backend/scripts/migrate_imitation_html_layout.py`
- Test: `backend/tests/test_imitation_html_layout_mode.py`

- [x] **Step 1: Write failing schema and creation tests**

Add tests asserting `TaskCreate` defaults to `content`, accepts `html_layout`, rejects unknown values, and `create_imitation_task` stores the requested value.

```python
def test_task_create_validates_imitation_mode():
    assert TaskCreate(name="每日仿写", pool_id=1).imitation_mode == "content"
    assert TaskCreate(name="每日仿写", pool_id=1, imitation_mode="html_layout").imitation_mode == "html_layout"
    with pytest.raises(ValidationError):
        TaskCreate(name="每日仿写", pool_id=1, imitation_mode="unknown")
```

- [x] **Step 2: Run tests and confirm the missing-field failure**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_html_layout_mode.py -v`
Expected: FAIL because `imitation_mode` is not defined.

- [x] **Step 3: Add the model, API and service contract**

Use `Literal["content", "html_layout"]` in the API schema and a non-null SQLAlchemy column with both Python and server defaults:

```python
imitation_mode = Column(
    String(32), nullable=False, default="content", server_default="content",
    comment="仿写模式: content=内容结构, html_layout=保留HTML版式",
)
```

Pass the field unchanged through `create_task` to `create_imitation_task` and expose it from `TaskResponse`.

- [x] **Step 4: Add an idempotent migration**

Create a migration that checks `information_schema.COLUMNS` and only executes:

```sql
ALTER TABLE imitation_tasks
ADD COLUMN imitation_mode VARCHAR(32) NOT NULL DEFAULT 'content'
COMMENT '仿写模式: content=内容结构, html_layout=保留HTML版式'
```

- [x] **Step 5: Run the focused tests**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_html_layout_mode.py -v`
Expected: schema and creation tests PASS.

### Task 2: Route HTML tasks through the existing DOM blueprint pipeline

**Files:**
- Modify: `backend/app/services/imitation_service.py`
- Test: `backend/tests/test_imitation_html_layout_mode.py`

- [x] **Step 1: Write failing generation tests**

Use patched Agent functions to capture `ArticleState` and assert:

```python
assert captured_state.reference_html == '<section style="border:1px solid"><p>原文</p></section>'
```

Also assert content mode leaves `reference_html` empty and HTML mode returns a clear failed result when `body_html` is absent.

- [x] **Step 2: Run tests and confirm HTML is not propagated**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_html_layout_mode.py -v`
Expected: FAIL because selected article dictionaries omit `body_html` and `ArticleState.reference_html` is unset.

- [x] **Step 3: Include and validate the HTML source**

Add `body_html` to each selected source article. In `execute_imitation_generation`, derive the mode once and set:

```python
reference_html = (reference_article.get("body_html") or "").strip()
if task.imitation_mode == "html_layout" and not reference_html:
    return {"success": False, "error": "HTML版式仿写需要投喂文章包含原始HTML", ...}

state.reference_html = reference_html if task.imitation_mode == "html_layout" else None
```

Keep Markdown structure analysis unchanged so existing style output and `structure_analysis` records remain compatible.

- [x] **Step 4: Run HTML service and routing regressions**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_html_layout_mode.py backend/tests/test_html_imitation_service.py -v`
Expected: all tests PASS.

### Task 3: Add the mode control to the imitation task UI

**Files:**
- Modify: `frontend/src/views/ImitationTasksView.vue`
- Create: `backend/tests/test_imitation_tasks_ui.py`

- [x] **Step 1: Write a failing frontend contract test**

Read the Vue source and assert that the form defaults to `content`, posts `imitation_mode`, renders both mode options and displays the selected mode in the task table.

```python
assert "imitation_mode: 'content'" in source
assert 'value="html_layout"' in source
assert "imitationModeLabels[row.imitation_mode]" in source
```

- [x] **Step 2: Run the test and confirm it fails**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_tasks_ui.py -v`
Expected: FAIL because the mode control does not exist.

- [x] **Step 3: Implement the UI control**

Extend the task interface and form state with `imitation_mode`. Add an Element Plus radio group with “内容结构仿写” and “HTML版式仿写”, and add a compact table column showing the selected mode. Reset the field to `content` whenever the dialog opens.

- [x] **Step 4: Verify Python and frontend builds**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_imitation_html_layout_mode.py backend/tests/test_imitation_tasks_ui.py backend/tests/test_html_imitation_service.py -q`
Expected: all tests PASS.

Run: `npm run build` in `frontend`
Expected: `vue-tsc` and Vite build complete successfully.

### Task 4: Final regression and migration validation

**Files:**
- Verify only; no new files expected.

- [x] **Step 1: Compile changed Python modules**

Run: `backend/venv/Scripts/python.exe -m compileall backend/app/models/mysql_models.py backend/app/api/v1/imitation.py backend/app/services/imitation_service.py backend/scripts/migrate_imitation_html_layout.py`
Expected: exit code 0.

- [x] **Step 2: Inspect final diff and unrelated runtime files**

Run: `git status --short` and `git diff --check`
Expected: implementation files are modified; `.superpowers/` and `backend/celerybeat-schedule.dat` remain unstaged and unchanged by this feature.

- [x] **Step 3: Commit the implementation**

```powershell
git add backend/app/models/mysql_models.py backend/app/api/v1/imitation.py backend/app/services/imitation_service.py backend/scripts/migrate_imitation_html_layout.py backend/tests/test_imitation_html_layout_mode.py backend/tests/test_imitation_tasks_ui.py frontend/src/views/ImitationTasksView.vue docs/superpowers/plans/2026-07-31-html-layout-imitation.md
git commit -m "实现HTML版式仿写任务"
```
