---
name: wechat-ai-content-operations
description: Operate the WeChat AI content platform through its authenticated REST API. Use for managing feed-source imitation, reusable format profiles and writing styles, knowledge bases, ERP product-image selection, article generation, review, optimization, comment/lead management, scheduled publishing tasks, and publication status without exposing credentials or disrupting existing schedules. Also used to check and restore the local platform services (API 8002 / frontend 5173 / Docker).
agent_created: true
---

# WeChat AI Content Operations

Operate the WeChat AI content platform at `<BASE_URL>/api/v1` — the local
deployment at `http://localhost:8002` (see `backend/app/config.py`), or a
deployed instance. It covers original articles, imitation articles, ERP-backed
product content, reusable format profiles and writing styles, scheduled tasks,
review/optimization workflows, WeChat comment and lead management, and WeChat
publishing.

For the full endpoint catalog with request/response shapes, load
`references/api_reference.md`. For a quick environment check, run
`scripts/check_health.py`.

## Operating Rules

1. Authenticate first. Call `POST /auth/login` with an authorized user's email
   and password. Send the returned bearer token in
   `Authorization: Bearer <access_token>` for later API calls. Refresh once on
   `401` with `POST /auth/refresh`; do not repeatedly retry invalid credentials.
2. Read before changing. Before creating, editing, disabling, or deleting a
   scheduled task, inspect `GET /scheduled-tasks`, its referenced feed sources,
   accounts, and knowledge bases.
3. Treat existing active tasks as production. Never change their schedule,
   account, publish mode, publish domain, ERP configuration, template rotation,
   or footer without explicit user approval that names the task.
4. Never request, reveal, copy, or place ERP `client_secret`, WeChat
   `AppSecret`, relay secrets, JWT secrets, model API keys, or storage keys in
   task payloads, prompts, generated articles, logs, or chat output. ERP
   credentials remain server-side.
5. Prefer `publish_mode: "draft"` for validation. Use `"direct"` only when the
   user explicitly asks for publication and has identified the target account
   and publish domain.
6. Do not manually enqueue or duplicate a run for a task that is `queued`,
   `running`, or `retrying`. The platform's persistent queue and retries are
   responsible for delivery.
7. Never claim publication succeeded until the publish-status endpoint reports
   a successful result.
8. When the API is unreachable, first check whether the local platform is
   running before reporting an outage (see "Local Platform Services").

## Check Service And Inventory

```http
GET /health
GET /health/db
GET /auth/me
GET /accounts
GET /knowledge-bases
GET /feed-sources
GET /format-profiles
GET /scheduled-tasks
GET /articles?page=1&page_size=20
GET /content-jobs
GET /reviews/pending
```

Use these endpoints to establish the tenant-scoped IDs. Do not infer an ID from
a display name when an API response is available.

## Local Platform Services

The local deployment runs FastAPI on port `8002` and the Vite frontend on port
`5173`, with Docker containers for MySQL/Postgres/Redis/MinIO/Celery.

- To check service availability, run `scripts/check_health.py` (optionally with
  `--base-url`).
- To start the local platform on this machine, run the repository script
  `scripts/start-local-platform.ps1` (idempotent; starts Docker Desktop if
  needed and skips services already listening).
- Do not attempt to restart containers while an article task is executing.

## Feed Source To Reusable Imitation Template

1. Create a source with `POST /feed-sources` using a name and article URL.
2. Fetch its content using `POST /feed-sources/{source_id}/fetch`.
3. Analyze it using `POST /feed-sources/{source_id}/analyze`.
4. Inspect `GET /feed-sources/{source_id}/articles` and `GET /format-profiles`.
5. When creating a task, use the resulting `format_profile_id`, or configure
   template rotation with only active format-profile IDs that belong to the
   same tenant.
6. For multi-source style simulation, use imitation pools
   (`/imitation/pools`): add sources, run `POST /imitation/pools/{pool_id}/analyze`
   for structural deep analysis, then create an imitation task with
   `POST /imitation/tasks` and control it with `execute` / `toggle`.

Do not pass raw source HTML into an article prompt unless the API explicitly
requires it. The platform preserves structure and style through its feed-source
and format-profile records.

## ERP Product Images

ERP sources are configured only by the server administrator. A client can list
safe source metadata and search products, but cannot read or supply credentials.

```http
GET /erp-product-sources
POST /erp-product-sources/{source_key}/products/search
Content-Type: application/json

{
  "pageNo": 1,
  "pageSize": 20,
  "keyword": ""
}
```

For scheduled content, use an `erp_image_config` rather than pasting an ERP
image URL. The scheduler selects one product and keeps a repeat history.

```json
{
  "source_key": "<server-listed-source-key>",
  "commodity_category": null,
  "repeat_after_days": 3,
  "image_count": 8,
  "selection_scope": "brand:<brand-key>"
}
```

Use the same `selection_scope` for a brand's public and private tasks when they
must not reuse the same product within the repeat window.

## Create A Scheduled Task

Only create a task after the user has supplied its name, schedule, target
account, publishing choice, content source, and whether it is a validation or
live task. Use one task for one delivery policy. For example, public and private
delivery should use separate tasks when their schedules or channels differ.

```http
POST /scheduled-tasks
Content-Type: application/json

{
  "name": "<descriptive task name>",
  "publish_times": ["13:00"],
  "day_of_week": -1,
  "account_ids": [<account-id>],
  "publish_mode": "draft",
  "publish_domain": "public",
  "content_type": "article",
  "layout_mode": "standard",
  "knowledge_base_ids": [<knowledge-base-id>],
  "format_profile_id": <optional-format-profile-id>,
  "feed_source_ids": [<optional-feed-source-id>],
  "style": "<optional-writing-style-template-id>",
  "enabled_image_methods": ["ERP"],
  "erp_image_config": {
    "source_key": "<server-listed-source-key>",
    "repeat_after_days": 3,
    "image_count": 8,
    "selection_scope": "brand:<brand-key>"
  }
}
```

Important fields:

- `publish_times`: `HH:MM` local Asia/Shanghai times.
- `day_of_week`: `-1` means every day; `0` through `6` restrict the weekday.
- `publish_mode`: `draft` saves to the WeChat draft box; `direct` requests
  actual publication.
- `publish_domain`: `public` or `private`; it must match the intended delivery
  route.
- `layout_mode`: `standard` for normal HTML/image-text content;
  `seamless_poster` only for an explicitly selected poster workflow.
- `footer_template`: optional fixed footer. Use the platform's structured
  `consultation_card_v1` JSON when a consultation card is required. Do not put
  contact information in image-generation prompts.
- Writing style options come from `GET /scheduled-tasks/writing-style-templates`.

## Template Rotation

To rotate several source templates, set `template_rotation_config` while
creating or explicitly updating a task:

```json
{
  "enabled": true,
  "profile_ids": [101, 102, 103],
  "basis": "publish_day",
  "uses_per_template": 1
}
```

- `basis: "publish_day"` changes template by publishing day.
- `basis: "publish_run"` changes template after each run.
- `uses_per_template` controls how many publishing days/runs use one template
  before advancing.

The API validates that every profile is active, belongs to the tenant, and comes
from a source article. Keep the order intentional because it is the cycle order.

## Monitor Scheduled Generation

1. Read `GET /scheduled-tasks` and `GET /articles`.
2. Locate the newest articles by their timestamps and task relationship.
3. For a specific generation task ID, use:

```http
GET /articles/{task_id}
GET /articles/{task_id}/progress
GET /articles/{task_id}/logs
GET /articles/{task_id}/publish-status
```

4. Report `queued`, `running`, `retrying`, `completed`, or `failed` accurately.
   A queued item is not lost. Do not change a queue solely because it has waited
   behind another long image-generation task.
5. On failure, read the persisted error/logs first. Do not retry or edit the
   task until the user authorizes an intervention.

## Article And Publication Workflow

For one-off work, create through `POST /articles/create`, monitor progress, then
use `POST /articles/{task_id}/publish-draft` only after content and target
account are confirmed. Use direct publishing only when the user's request is
unambiguous. Never claim publication succeeded until the publish-status endpoint
reports a successful result. Article quality metrics live under
`/articles/{article_id}/metrics` and `/quality-evaluations`.

## Review And Optimization Workflow

- Pending approvals: `GET /reviews/pending`; submit a decision with
  `POST /reviews` (`approved` / `rejected`). Rejection drives the task state
  machine; attach modification notes when the API supports them.
- Optimization: `GET /optimizations/candidates` lists articles by quality score;
  `POST /optimizations/{id}/approve|reject|regenerate` manage optimization
  drafts; `GET /optimizations/{id}/comparison` shows before/after effect.

## WeChat Interaction: Comments, Leads, Messages

- Sync comments with `POST /comments/sync` (by `msg_data_id`) or
  `POST /comments/sync-by-article` (by local article ID). Auto-reply and
  auto-message settings per account live at `GET/PUT /comments/auto-config/{account_id}`.
- Reply with `POST /comments/reply`; toggle featured with
  `POST /comments/toggle-favorite`.
- Leads: `POST /leads/sync` creates leads from comments (returns a `job_id`;
  poll `GET /leads/sync-jobs/{job_id}`). Close leads with
  `POST /leads/{lead_id}/close`; send contact packages with
  `POST /leads/{lead_id}/deliveries` and monitor `GET /deliveries/{delivery_id}`.
- Messages: `POST /messages/send-text`, `send-image`, `send-contact`.
- Contact packages: manage via `GET/POST/PUT` on `/contact-packages`; media
  assets via `/media-assets`.
- Note: comment/lead/message endpoints require an authenticated (认证) service
  account and corresponding WeChat permissions; report permission errors rather
  than retrying.

## WeChat Article Sync

- List synced articles with `GET /wechat-articles`; sync with
  `POST /wechat-articles/sync-drafts` and `/sync-published`.
- Reading a synced article (`GET /wechat-articles/{article_id}`) optionally
  fetches body text in real time.

## Knowledge Bases And Assets

- List knowledge bases with `GET /knowledge-bases` and inspect their documents
  before binding them to a task.
- Create a knowledge base with `POST /knowledge-bases`; upload documents using
  `POST /knowledge-bases/{kb_id}/documents` as multipart form data. Search with
  `GET /knowledge-bases/{kb_id}/search` or cross-KB `GET /knowledge-bases/search/all`.
- List assets with `GET /assets`. Upload via `POST /assets/upload` only when the
  user provides the asset and its intended use. Generated content assets live
  under `/content-assets`.
- Do not delete knowledge bases, source articles, assets, or published articles
  unless the user explicitly identifies the target and confirms deletion.

## Content Jobs Queue

`GET /content-jobs` lists queue items; `POST /content-jobs/{job_id}/transition`
drives state (`queue/cancel/pause/resume/approve/reject/schedule/publish`).
Deletion is only allowed for `cancelled/failed/rejected` jobs. Version history
is available at `GET /content-jobs/{job_id}/versions`. Prefer the
scheduled-task flow over manual job enqueueing for recurring content.

## Failure Handling

- `401`: refresh authentication once, then ask for authorized credentials.
- `403`: report the permission or tenant boundary; do not try other IDs.
- `404`: the resource does not exist for this tenant; re-inventory before acting.
- `422`: report the validation details and correct the payload, never bypass it.
- `429`, timeouts, or upstream `5xx`: report a transient upstream failure and
  rely on the platform's scheduled retry policy for scheduled jobs.
- If a publish response is ambiguous, do not issue another publish request;
  inspect the article's publish status first to prevent duplicate publication.
- `500` on `POST /articles/create` with the article row left `pending`: the API
  process may be crashing on emoji `print()` when stdout uses GBK (Windows
  Chinese locale). Fix: `backend/app/main.py` reconfigures stdout/stderr to
  UTF-8 and `scripts/start-local-platform.ps1` sets `PYTHONIOENCODING=utf-8`
  (both already patched). If the symptom reappears, ensure the running API
  process was started by that script, then retry. Deleting stuck `pending`
  rows is safe via `DELETE /articles/{article_id}`.
- `500` on article create can also leave the request running for minutes (the
  pipeline is synchronous): the article is still being generated, so poll
  `GET /articles/{task_id}` instead of treating the delay as a failure.

## Local Deployment Notes

- Local API: `http://localhost:8002` (not 8000 — README is stale), Vite frontend
  `5173`. Restore with `scripts/start-local-platform.ps1` (idempotent).
- Text generation in this deployment uses the kuai relay
  (`TEXT_GENERATION_PROVIDER_CHAIN=kuai,dashscope`, model `gpt-5-mini`), image
  generation via kuai OpenAI-compatible provider — no DashScope key required.
- WeChat API channel is `relay` mode: draft/publish works, but draft-box sync,
  comments, messages, and metrics are disabled until the relay provides them.
  Do not treat "sync disabled" as a publish failure; rely on article
  `status=draft_saved` / `phase=DRAFT_SAVED` as the confirmation.
- `POST /articles/create` runs the whole pipeline synchronously; a long request
  is normal. `GET /articles/{task_id}` detail and `/publish-status` are the
  monitoring endpoints (publish-status currently deprecated → use detail status).

