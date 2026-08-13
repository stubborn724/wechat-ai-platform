---
name: wechat-ai-content-operations
description: Operate the WeChat AI content platform through its authenticated REST API. Use for managing feed-source imitation, knowledge bases, ERP product-image selection, article generation, scheduled publishing tasks, and publication status without exposing credentials or disrupting existing schedules.
---

# WeChat AI Content Operations

Use this skill to operate a local or deployed WeChat AI content platform at
`<BASE_URL>/api/v1`. It supports original articles, imitation articles,
ERP-backed product content, reusable source format profiles, scheduled tasks,
and WeChat publishing.

## Operating Rules

1. Authenticate first. Call `POST /auth/login` with an authorized user's
   email and password. Send the returned bearer token in
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

## Check Service And Inventory

```http
GET /health
GET /auth/me
GET /accounts
GET /knowledge-bases
GET /feed-sources
GET /format-profiles
GET /scheduled-tasks
GET /articles?page=1&page_size=20
```

Use these endpoints to establish the tenant-scoped IDs. Do not infer an ID from
a display name when an API response is available.

## Feed Source To Reusable Imitation Template

1. Create a source with `POST /feed-sources` using a name and article URL.
2. Fetch its content using `POST /feed-sources/{source_id}/fetch`.
3. Analyze it using `POST /feed-sources/{source_id}/analyze`.
4. Inspect `GET /feed-sources/{source_id}/articles` and `GET /format-profiles`.
5. When creating a task, use the resulting `format_profile_id`, or configure
   template rotation with only active format-profile IDs that belong to the
   same tenant.

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
reports a successful result.

## Knowledge Bases And Assets

- List knowledge bases with `GET /knowledge-bases` and inspect their documents
  before binding them to a task.
- Create a knowledge base with `POST /knowledge-bases`; upload documents using
  `POST /knowledge-bases/{kb_id}/documents` as multipart form data.
- List assets with `GET /assets`. Upload via `POST /assets/upload` only when the
  user provides the asset and its intended use.
- Do not delete knowledge bases, source articles, assets, or published articles
  unless the user explicitly identifies the target and confirms deletion.

## Failure Handling

- `401`: refresh authentication once, then ask for authorized credentials.
- `403`: report the permission or tenant boundary; do not try other IDs.
- `422`: report the validation details and correct the payload, never bypass it.
- `429`, timeouts, or upstream `5xx`: report a transient upstream failure and
  rely on the platform's scheduled retry policy for scheduled jobs.
- If a publish response is ambiguous, do not issue another publish request;
  inspect the article's publish status first to prevent duplicate publication.
