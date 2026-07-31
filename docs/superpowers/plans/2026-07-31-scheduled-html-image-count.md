# Scheduled HTML Image Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the number of generated images in scheduled HTML imitation configurable per task while preserving the existing five-image default.

**Architecture:** Persist `html_image_count` on `ScheduledTask`, copy it to `ArticleState.max_generated_images` at execution time, and let the existing deterministic HTML slot selector enforce it. The setting is ignored outside HTML imitation and remains independent from ERP image configuration.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Celery, BeautifulSoup, pytest, Vue 3, TypeScript, Element Plus.

---

### Task 1: Add the task-level contract and migration

**Files:**
- Modify: `backend/app/models/mysql_models.py`
- Modify: `backend/app/api/v1/scheduled_tasks.py`
- Create: `backend/scripts/migrate_scheduled_html_image_count.py`
- Create: `backend/tests/test_scheduled_html_image_count.py`

- [ ] Write tests proving the API defaults to 5, accepts 19, rejects values outside 1-30, and the ORM field defaults to 5.
- [ ] Run the focused test and confirm the missing-field failures.
- [ ] Add `html_image_count` to the SQLAlchemy model and create/update/response schemas using `Field(default=5, ge=1, le=30)`.
- [ ] Persist the create payload and add an idempotent migration for `INT NOT NULL DEFAULT 5`.
- [ ] Run the focused tests and migration twice.

### Task 2: Route the value into HTML slot selection

**Files:**
- Modify: `backend/app/schemas/article.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/app/services/article_agent_service.py`
- Test: `backend/tests/test_scheduled_html_image_count.py`
- Test: `backend/tests/test_html_imitation_service.py`

- [ ] Write a failing test that captures `max_generated_images=19` passed to `select_html_image_slots`.
- [ ] Add the five-image default to `ArticleState` and assign the task value when the scheduled state is created.
- [ ] Replace the hard-coded `5` in the HTML Agent with the validated state value.
- [ ] Run HTML imitation and scheduled task regressions.

### Task 3: Expose the setting in the scheduled task UI

**Files:**
- Modify: `frontend/src/views/ScheduledTasksView.vue`
- Test: `backend/tests/test_scheduled_html_image_count.py`

- [ ] Write a failing source contract test for default, edit restore, payload and the 1-30 input control.
- [ ] Add `html_image_count` to the task interface and form state.
- [ ] Render the numeric control only for feed-backed article tasks.
- [ ] Run the frontend contract test and `npm run build`.

### Task 4: Validate and commit

**Files:**
- Verify all files above.

- [ ] Run focused pytest regressions and Python compileall.
- [ ] Run `git diff --check` and review the staged file list.
- [ ] Commit with the Chinese message `支持定时HTML仿写图片数量配置`.
