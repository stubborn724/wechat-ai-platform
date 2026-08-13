# Scheduled Template Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an opted-in scheduled task cycle selected source-article format templates by publish day or publish occurrence without changing existing tasks.

**Architecture:** Store task-level rotation configuration and a monotonically increasing configuration version on `scheduled_tasks`. Freeze the selected profile ID and rotation version onto each `scheduled_task_runs` row when the slot is created, then the executor reads that snapshot for all attempts and retries. A dedicated service owns validation and deterministic selection; the API and executor remain orchestration layers.

**Tech Stack:** FastAPI/Pydantic, SQLAlchemy/MySQL, Celery, Vue 3 with Element Plus, pytest.

---

### Task 1: Add deterministic rotation domain service

**Files:**
- Create: `backend/app/services/scheduled_template_rotation_service.py`
- Test: `backend/tests/test_scheduled_template_rotation_service.py`

- [ ] Write tests for disabled rotation, daily rotation, run rotation, `uses_per_template`, and invalid duplicated or single-profile configurations.
- [ ] Implement `normalize_template_rotation_config`, `validate_rotation_profile_ids`, and `select_rotation_profile_id`.
- [ ] Run `pytest tests/test_scheduled_template_rotation_service.py -q` and confirm all cases pass.

### Task 2: Persist configuration and per-run snapshots

**Files:**
- Modify: `backend/app/models/mysql_models.py`
- Create: `backend/scripts/migrate_scheduled_template_rotation.py`
- Test: `backend/tests/test_scheduled_template_rotation_service.py`

- [ ] Add nullable `template_rotation_config` and version columns to `ScheduledTask`.
- [ ] Add `format_profile_id` and rotation version columns to `ScheduledTaskRun`.
- [ ] Add an idempotent migration that creates only missing columns and the lookup index, without changing existing task rows.
- [ ] Execute the migration twice and confirm the second run is a no-op.

### Task 3: Expose and validate task rotation configuration

**Files:**
- Modify: `backend/app/api/v1/scheduled_tasks.py`
- Modify: `backend/app/api/v1/feed_sources.py`
- Modify: `frontend/src/api/types.ts`
- Test: `backend/tests/test_scheduled_template_rotation_api_contract.py`

- [ ] Add the rotation input/output schema and validate that enabled rotation has at least two active, tenant-owned source templates.
- [ ] Increment the configuration version only when the normalized configuration changes; set the fallback task template to the first selected rotation template.
- [ ] Enrich template options with source article and feed source labels so users can distinguish similarly named templates.
- [ ] Run API contract tests.

### Task 4: Freeze the template at slot creation and consume it in execution

**Files:**
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Test: `backend/tests/test_scheduled_template_rotation_executor.py`

- [ ] Create a queued run with the deterministic profile selected from all earlier slots of the same rotation version.
- [ ] For `publish_day`, count earlier distinct scheduled dates; for `publish_run`, count earlier time slots.
- [ ] Make `_scheduled_article` prefer `run.format_profile_id`; use its source article as the primary imitation reference only when rotation is enabled.
- [ ] Verify retries retain the frozen profile ID and existing tasks with no rotation configuration retain the old path.

### Task 5: Add the task-form controls

**Files:**
- Modify: `frontend/src/views/ScheduledTasksView.vue`
- Test: `backend/tests/test_scheduled_template_rotation_ui_contract.py`

- [ ] Add a disabled-by-default template rotation switch.
- [ ] When enabled, allow ordered multi-selection of source templates, move-up/move-down controls, basis selection, and the positive integer usage count.
- [ ] Send the new configuration without changing existing task payloads when rotation is disabled.
- [ ] Build the frontend and run the UI contract test.

### Task 6: Full verification and controlled rollout

**Files:**
- Test: relevant scheduled task, format profile, and rotation suites

- [ ] Run the new migration twice.
- [ ] Run targeted backend tests and `npm run build`.
- [ ] Restart only the API and the two Celery workers after migration; leave task data and schedules unchanged.
- [ ] Check health endpoints and confirm existing tasks have `template_rotation_config = NULL`.
