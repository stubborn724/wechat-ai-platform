# Article Generation Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce scheduled article end-to-end latency without reducing ERP image quality or weakening WeChat delivery idempotency.

**Architecture:** Move provider timing policy and circuit state into focused services consumed by the existing image routing service. Replace global scheduled-run admission with a bounded, task-aware selector while preserving the database claim protocol. Reuse the existing immutable format-profile mechanism for the fixed Xiuman feed source and preserve per-account delivery records while parallelizing only independent draft requests.

**Tech Stack:** Python 3.11, FastAPI, Celery, SQLAlchemy, MySQL, Redis, httpx, pytest.

---

### Task 1: Provider Timeout and Circuit Policy

**Files:**
- Create: `backend/app/services/image_provider_health_service.py`
- Modify: `backend/app/config.py:110-130`
- Modify: `backend/app/services/openai_compatible_image_provider.py:40-95`
- Modify: `backend/app/services/volcengine_ark_image_provider.py:45-80`
- Modify: `backend/app/services/image_generation_service.py:55-130`
- Test: `backend/tests/test_image_generation_fallback.py`

- [ ] **Step 1: Write failing tests for circuit opening and provider-specific timeout lookup**

```python
@pytest.mark.asyncio
async def test_circuit_open_provider_is_skipped_until_cooldown_expires():
    service, primary, fallback = build_service_with_memory_health()
    await service.generate(ImageGenerationRequest(prompt="first"))
    await service.generate(ImageGenerationRequest(prompt="second"))
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2

def test_provider_timeout_prefers_provider_specific_setting():
    settings = SimpleNamespace(image_generation_timeout_seconds=1800,
        image_generation_primary_timeout_seconds=120)
    assert resolve_image_provider_timeout(settings, "kuai_openai_compatible") == 120
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the health service and timeout resolver do not exist**

Run: `pytest -q backend/tests/test_image_generation_fallback.py -k "circuit or provider_timeout"`

Expected: collection or assertion failure mentioning the missing health-service behavior.

- [ ] **Step 3: Implement a Redis-backed health service with in-memory fallback**

```python
class ImageProviderHealthService:
    def allow_request(self, provider: str, operation: str, now: float | None = None) -> bool: ...
    def record_success(self, provider: str, operation: str) -> None: ...
    def record_failure(self, provider: str, operation: str, category: ImageErrorCategory) -> None: ...
```

Only `temporary`, `upstream`, and `truncated_response` failures increment the breaker. The third consecutive failure opens a ten-minute cooldown; a success clears the failure count. Redis faults must fail open so image generation remains available.

- [ ] **Step 4: Add provider-specific timeout settings and pass them to each provider**

```python
image_generation_primary_timeout_seconds: int = 120
image_generation_secondary_timeout_seconds: int = 150
image_generation_ark_timeout_seconds: int = 180
```

`OpenAICompatibleImageProvider` receives a configured timeout at construction; `VolcengineArkImageProvider` reads only the Ark-specific setting. The old global setting remains as a backward-compatible fallback.

- [ ] **Step 5: Integrate health checks around each provider invocation**

Before calling a provider, skip it when its circuit is open and append a temporary `ImageProviderError` summary. Record success after a valid generated image and record a failure only when an `ImageProviderError` is caught. Preserve the existing no-fallback behavior for authentication, invalid request, and configuration errors.

- [ ] **Step 6: Run focused tests and existing fallback suite**

Run: `pytest -q backend/tests/test_image_generation_fallback.py backend/tests/test_openai_compatible_image_provider.py backend/tests/test_volcengine_ark_image_provider.py`

Expected: all tests pass.

### Task 2: Scheduled Run Admission Slots

**Files:**
- Create: `backend/app/services/scheduled_run_admission_service.py`
- Modify: `backend/app/config.py:130-140`
- Modify: `backend/app/tasks/scheduled_task_executor.py:250-310`
- Modify: `backend/app/tasks/scheduled_task_executor.py:500-545`
- Modify: `docker-compose.yml:91-117`
- Test: `backend/tests/test_scheduled_task_retry.py`

- [ ] **Step 1: Write failing selector tests for two global slots and one slot per task**

```python
def test_selector_admits_two_distinct_tasks_when_two_slots_are_available():
    selected = select_admissible_scheduled_runs([run(1), run(2), run(3)], max_active_runs=2)
    assert [item.id for item in selected] == [1, 2]

def test_selector_never_admits_second_run_of_task_already_running():
    selected = select_admissible_scheduled_runs([running(9), queued(9), queued(10)], max_active_runs=2)
    assert [item.task_id for item in selected] == [10]
```

- [ ] **Step 2: Run the selector tests and confirm they fail because the bounded selector is absent**

Run: `pytest -q backend/tests/test_scheduled_task_retry.py -k "admits_two or second_run_of_task"`

Expected: import failure for `select_admissible_scheduled_runs`.

- [ ] **Step 3: Implement a pure admission policy**

```python
def select_admissible_scheduled_runs(runs, *, max_active_runs: int) -> list[ScheduledTaskRun]: ...
```

Count in-flight runs first, reserve their `task_id` values, then select oldest queued runs whose task ID is not reserved until the configured global slot limit is reached. Keep the existing `select_next_waiting_scheduled_run` as a compatibility wrapper returning the first admitted run.

- [ ] **Step 4: Dispatch every newly admissible queued run under existing row locks**

Update `_dispatch_next_waiting_scheduled_run` to dispatch all newly admitted runs in one locked queue scan. Each individual `_enqueue_scheduled_run` retains the existing database row lock and claim protocol. A failed dispatch must not permit another run from that task to leapfrog it.

- [ ] **Step 5: Configure two Celery execution slots only after admission is bounded**

Add `SCHEDULED_TASK_MAX_ACTIVE_RUNS=2`. Change the dedicated worker to `--pool=threads --concurrency=2`; its task code uses fresh database sessions per run, and the admission lock prevents duplicate run execution. Keep the default one-slot setting available for immediate rollback.

- [ ] **Step 6: Run scheduled retry and publication regression tests**

Run: `pytest -q backend/tests/test_scheduled_task_retry.py backend/tests/test_scheduled_publish_result.py backend/tests/test_scheduled_template_rotation_executor.py`

Expected: all tests pass.

### Task 3: Xiuman Fixed Feed Format Reuse

**Files:**
- Create: `backend/scripts/bind_xiuman_format_profiles.py`
- Modify: `backend/tests/test_format_profile_task_binding.py`
- Test: `backend/tests/test_format_profile_task_binding.py`

- [ ] **Step 1: Write a failing policy test describing the fixed Xiuman binding**

```python
def test_xiuman_binding_uses_saved_profile_without_reference_image_understanding():
    task = SimpleNamespace(id=11, format_profile_id=6)
    assert should_use_format_profile(task) is True
```

- [ ] **Step 2: Run the test to verify the profile binding is currently absent from the production task configuration**

Run: `pytest -q backend/tests/test_format_profile_task_binding.py -k xiuman_binding`

Expected: failure until the task-binding migration script and fixture describe the explicit binding.

- [ ] **Step 3: Implement an idempotent binding script**

The script queries source article `#1`, resolves its latest active `ArticleFormatProfile`, and updates only `#11` and `#13` when they still point to that fixed feed article. It must print a dry-run summary by default and require `--apply` for database writes. It must never create a profile or alter task topics, ERP configuration, accounts, or publish mode.

- [ ] **Step 4: Run format profile tests**

Run: `pytest -q backend/tests/test_format_profile_task_binding.py backend/tests/test_format_profile_persistence_service.py backend/tests/test_scheduled_template_rotation_executor.py`

Expected: all tests pass.

### Task 4: Bounded Parallel Draft Delivery

**Files:**
- Create: `backend/app/services/scheduled_delivery_service.py`
- Modify: `backend/app/config.py:130-145`
- Modify: `backend/app/tasks/scheduled_task_executor.py:2245-2400`
- Modify: `backend/tests/test_scheduled_publish_result.py`

- [ ] **Step 1: Write failing delivery tests for bounded concurrency and completed-account skipping**

```python
def test_draft_delivery_submits_only_pending_accounts_with_configured_limit(monkeypatch):
    result = deliver_pending_drafts(article_id=24, account_ids=[1, 2, 3], max_workers=2)
    assert result.max_parallel_calls == 2

def test_retry_only_delivers_accounts_missing_success_result():
    assert pending_delivery_account_ids([1, 2], {"24:1": success_result}) == [2]
```

- [ ] **Step 2: Run focused delivery tests and confirm the bounded delivery service is absent**

Run: `pytest -q backend/tests/test_scheduled_publish_result.py -k "configured_limit or pending_accounts"`

Expected: import failure for the new service functions.

- [ ] **Step 3: Implement independent account delivery work units**

Each worker opens its own `MysqlSessionLocal`, reloads article, task, and run, rechecks `delivery_results`, calls `publish_article`, and persists exactly one account result. Use a `ThreadPoolExecutor(max_workers=2)` only for draft mode. Direct publish stays serial until separate external idempotency validation is introduced.

- [ ] **Step 4: Keep ambiguous and partial semantics unchanged**

The work unit must persist `partial` and `ambiguous` before returning an error. The coordinator must surface the first failure only after all already-started work units complete, so the run record contains every known account result.

- [ ] **Step 5: Run delivery tests**

Run: `pytest -q backend/tests/test_scheduled_publish_result.py backend/tests/test_wechat_gateway_policy.py`

Expected: all tests pass.

### Task 5: Quality-Preserving Verification and Scheduled Draft Test

**Files:**
- Modify: `.env` only through documented per-provider timeout and active-run settings
- Modify: `docker-compose.yml` only after tests pass
- Test: `backend/tests/test_poster_article_service.py`, `backend/tests/test_scheduled_product_scene_service.py`, `backend/tests/test_scheduled_publication_policy.py`

- [ ] **Step 1: Run the full focused quality regression suite**

Run: `PYTHONPATH=. pytest -q tests/test_poster_article_service.py tests/test_footer_template_service.py tests/test_scheduled_product_scene_service.py tests/test_scheduled_publication_policy.py tests/test_scheduled_image_quality_service.py tests/test_image_generation_fallback.py tests/test_scheduled_task_retry.py tests/test_scheduled_publish_result.py`

Expected: all tests pass.

- [ ] **Step 2: Restart only the scheduled Worker and confirm its effective concurrency and configuration**

Run: `docker compose up -d --force-recreate celery-scheduled-worker && docker logs --tail 80 wechat-celery-scheduled-worker`

Expected: worker consumes the `scheduled` queue with the configured concurrency and no startup configuration errors.

- [ ] **Step 3: Create draft-only validation runs**

Create separate disabled-after-run scheduled tasks for: one Xiuman ERP five-image article, one seamless poster article, one three-article/five-account draft task, and one HTML multi-image draft task. Use the existing Xiuman account only for Xiuman validation. Record run IDs and stage metrics.

- [ ] **Step 4: Inspect generated drafts and metrics before enabling recurring behavior**

Compare generated product identity, scene consistency, title format, poster layout, image count, and all account delivery results with the pre-change baseline. Do not enable a recurring test task unless all drafts are saved successfully and quality checks pass.
