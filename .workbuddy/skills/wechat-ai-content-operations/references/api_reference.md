# Platform REST API Reference

Base URL: `<BASE_URL>/api/v1` (local default `http://localhost:8002`, see
`backend/app/config.py`). All endpoints below are prefixed with `/api/v1`
unless noted. Requests that change state require
`Authorization: Bearer <access_token>` obtained from `POST /auth/login`.

## Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | Login; returns access/refresh tokens (also HttpOnly cookies) |
| POST | `/auth/register` | Register a new user (auto-creates default tenant) |
| POST | `/auth/refresh` | Refresh access token (body or cookie) |
| GET | `/auth/me` | Current user info |
| POST | `/auth/logout` | Logout; clears auth cookies |

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service health `{status: ok}` |
| GET | `/health/db` | MySQL and PostgreSQL connectivity check |

## Accounts (公众号)

| Method | Path | Description |
|---|---|---|
| GET | `/accounts` | List tenant accounts (paged, status filter) |
| GET | `/accounts/{account_id}` | Account detail |
| POST | `/accounts` | Create account (app_id conflict → 409) |
| PUT | `/accounts/{account_id}` | Update account |
| DELETE | `/accounts/{account_id}` | Soft-delete account |

## Feed Sources (投喂源) and Format Profiles

| Method | Path | Description |
|---|---|---|
| GET | `/feed-sources` | List feed sources |
| POST | `/feed-sources` | Create source (auto first fetch; slug conflict → 409) |
| GET | `/feed-sources/{source_id}` | Source detail |
| PUT | `/feed-sources/{source_id}` | Update source |
| DELETE | `/feed-sources/{source_id}` | Delete source and its articles |
| POST | `/feed-sources/{source_id}/fetch` | Trigger article fetch |
| POST | `/feed-sources/{source_id}/analyze` | AI analyze writing style |
| GET | `/feed-sources/{source_id}/articles` | List fetched articles |
| POST | `/feed-sources/{source_id}/articles` | Manually add an article |
| POST | `/feed-sources/{source_id}/articles/{article_id}/format-profiles` | Analyze article into a reusable format template |
| GET | `/format-profiles` | List tenant format templates |

## Imitation Pools and Tasks (仿写)

| Method | Path | Description |
|---|---|---|
| GET | `/imitation/pools` | List pools with source counts |
| POST | `/imitation/pools` | Create pool |
| DELETE | `/imitation/pools/{pool_id}` | Deactivate pool |
| GET | `/imitation/pools/{pool_id}/sources` | List pool sources |
| POST | `/imitation/pools/{pool_id}/sources` | Add source to pool |
| DELETE | `/imitation/pools/{pool_id}/sources/{source_id}` | Remove source |
| POST | `/imitation/pools/{pool_id}/analyze` | Deep structural analysis of pool sources |
| GET | `/imitation/tasks` | List imitation tasks |
| POST | `/imitation/tasks` | Create imitation task |
| POST | `/imitation/tasks/{task_id}/execute` | Run task immediately |
| POST | `/imitation/tasks/{task_id}/toggle?action=pause\|resume` | Pause/resume |
| DELETE | `/imitation/tasks/{task_id}` | Delete (marks completed) |

## ERP Product Images

| Method | Path | Description |
|---|---|---|
| GET | `/erp-product-sources` | List server-configured ERP brand sources |
| POST | `/erp-product-sources/{source_key}/products/search` | Search products (paged, multi-filter) |
| POST | `/erp-product-sources/{source_key}/images/import` | Import one quote image into local asset library |
| POST | `/erp-product-sources/{source_key}/images/import-batch` | Batch import (max 20, dedup per image) |

## Articles (生成管线)

| Method | Path | Description |
|---|---|---|
| POST | `/articles/create` | Create article / pure image / video via multi-agent pipeline (optional direct publish) |
| GET | `/articles` | List articles (paged, status filter) |
| GET | `/articles/{task_id}` | Article detail by task_id |
| DELETE | `/articles/{article_id}` | Delete article |
| GET | `/articles/{task_id}/progress` | SSE generation progress stream |
| GET | `/articles/{task_id}/logs` | Agent execution logs |
| GET | `/articles/{task_id}/publish-status` | Publication status |
| POST | `/articles/{task_id}/publish-draft` | Publish to WeChat (`draft` box or `direct`) |
| POST | `/articles/{task_id}/set-msg-data-id` | Manually bind WeChat msg_data_id |
| GET | `/articles/{article_id}/metrics/latest` | Latest reading metrics |
| GET | `/articles/{article_id}/metrics` | Historical metrics trend |
| POST | `/articles/{article_id}/metrics/sync` | Trigger metrics sync |
| GET | `/articles/{article_id}/quality/latest` | Latest AI quality score |
| GET | `/articles/{article_id}/quality-evaluations` | Scoring history |
| POST | `/articles/{article_id}/quality-evaluations` | Trigger scoring |
| POST | `/articles/{article_id}/optimization-drafts` | Create optimization draft |

## Reviews (审核)

| Method | Path | Description |
|---|---|---|
| GET | `/reviews` | Review records (decision filter) |
| GET | `/reviews/pending` | Tasks pending review for current user |
| POST | `/reviews` | Submit decision (`approved` / `rejected`); drives task state machine |

## Optimizations (优化)

| Method | Path | Description |
|---|---|---|
| GET | `/optimizations/candidates` | Optimization candidates by quality score |
| GET | `/optimizations` | Optimization records |
| GET | `/optimizations/{optimization_id}` | Record detail |
| POST | `/optimizations/{optimization_id}/approve` | Approve draft |
| POST | `/optimizations/{optimization_id}/reject` | Reject draft |
| POST | `/optimizations/{optimization_id}/regenerate` | Regenerate (Celery) |
| GET | `/optimizations/{optimization_id}/comparison` | Before/after comparison |

## Content Jobs (内容任务队列)

| Method | Path | Description |
|---|---|---|
| GET | `/content-jobs` | Job list (paged, status filter) |
| POST | `/content-jobs` | Create job (idempotency key conflict → 409) |
| GET | `/content-jobs/{job_id}` | Job detail incl. publish attempts |
| DELETE | `/content-jobs/{job_id}` | Delete (only cancelled/failed/rejected) |
| POST | `/content-jobs/{job_id}/transition` | State transition (`queue/cancel/pause/resume/approve/reject/schedule/publish`) |
| GET | `/content-jobs/{job_id}/versions` | Content version history |

## Scheduled Tasks (定时任务)

| Method | Path | Description |
|---|---|---|
| GET | `/scheduled-tasks` | List tasks (incl. slot records) |
| POST | `/scheduled-tasks` | Create task (template rotation, watermark snapshot, ERP image config) |
| PUT | `/scheduled-tasks/{task_id}` | Update task |
| DELETE | `/scheduled-tasks/{task_id}` | Delete task |
| POST | `/scheduled-tasks/{task_id}/toggle` | Enable/disable |
| GET | `/scheduled-tasks/writing-style-templates` | Built-in writing style options |

## Knowledge Bases (知识库)

| Method | Path | Description |
|---|---|---|
| GET | `/knowledge-bases` | List KBs |
| POST | `/knowledge-bases` | Create KB |
| GET | `/knowledge-bases/{kb_id}` | KB detail |
| DELETE | `/knowledge-bases/{kb_id}` | Soft-delete KB |
| POST | `/knowledge-bases/{kb_id}/documents` | Upload document (PDF/DOCX/MD/TXT; auto chunk + vectorize) |
| GET | `/knowledge-bases/{kb_id}/documents` | List documents |
| GET | `/knowledge-bases/{kb_id}/documents/{doc_id}` | Document detail |
| GET | `/knowledge-bases/{kb_id}/documents/{doc_id}/content` | Read chunk content |
| DELETE | `/knowledge-bases/{kb_id}/documents/{doc_id}` | Delete document and vectors |
| GET | `/knowledge-bases/{kb_id}/search` | Vector similarity search in KB |
| GET | `/knowledge-bases/search/all` | Cross-KB search |

## Assets (素材库)

| Method | Path | Description |
|---|---|---|
| GET | `/assets` | List assets (type/tag filter, preview URLs) |
| POST | `/assets/upload` | Upload (auto watermark per tenant config) |
| GET | `/assets/{asset_id}` | Asset detail |
| GET | `/assets/{asset_id}/file` | Redirect to MinIO file |
| POST | `/assets/bulk-delete` | Batch delete (max 100, returns failures) |
| DELETE | `/assets/{asset_id}` | Delete asset + object storage file |
| POST | `/assets/{asset_id}/watermark` | Add watermark → new version |
| DELETE | `/assets/{asset_id}/watermark` | Remove watermark, restore original |

## Content Assets (生成素材)

| Method | Path | Description |
|---|---|---|
| GET | `/content-assets` | List generated assets (job/content_type/asset_type filter) |
| GET | `/content-assets/{asset_id}` | Detail (incl. file_url) |
| GET | `/content-assets/{asset_id}/file` | Redirect to MinIO file |
| POST | `/content-assets/{asset_id}/regenerate` | Regenerate (marks pending, triggers Celery) |
| DELETE | `/content-assets/{asset_id}` | Delete (MinIO + DB) |

## WeChat Articles (微信文章同步)

| Method | Path | Description |
|---|---|---|
| GET | `/wechat-articles` | List synced articles (draft/published) |
| POST | `/wechat-articles/sync-drafts` | Sync WeChat draft box |
| POST | `/wechat-articles/sync-published` | Sync published articles |
| GET | `/wechat-articles/{article_id}` | Synced article detail (optional real-time body fetch) |
| DELETE | `/wechat-articles/{article_id}` | Soft-delete sync record |

## Comments (评论)

| Method | Path | Description |
|---|---|---|
| GET | `/comments` | Comment list (paged, filters) |
| GET | `/comments/{comment_id}` | Comment detail |
| POST | `/comments/sync` | Sync comments by msg_data_id (with auto reply/message) |
| POST | `/comments/sync-by-article` | Sync by local article ID |
| GET | `/comments/debug-wechat-api` | Debug: raw WeChat comment API data |
| POST | `/comments/reply` | Reply (WeChat + local) |
| POST | `/comments/toggle-favorite` | Feature/unfeature comment |
| GET | `/comments/auto-config/{account_id}` | Auto reply/message config |
| PUT | `/comments/auto-config/{account_id}` | Create/update auto config |

## Messages (私信)

| Method | Path | Description |
|---|---|---|
| GET | `/messages` | Message records |
| POST | `/messages/send-text` | Send text message |
| POST | `/messages/send-image` | Send image message |
| POST | `/messages/send-contact` | Send contact + QR (text+image) |

## Leads (线索工作台)

| Method | Path | Description |
|---|---|---|
| GET | `/leads/queue-stats` | Queue counts |
| GET | `/leads` | Lead list (queue/intent/operator filter) |
| GET | `/leads/{lead_id}` | Lead detail |
| POST | `/leads/sync` | Sync comments → create leads (background; returns job_id) |
| GET | `/leads/sync-jobs/{job_id}` | Sync job status |
| POST | `/leads/{lead_id}/public-reply` | Public comment reply (WeChat first) |
| POST | `/leads/{lead_id}/generate-reply` | Generate reply (V1 template) |
| POST | `/leads/{lead_id}/close` | Close lead |
| POST | `/leads/{lead_id}/check-eligibility` | Three-state eligibility check |
| POST | `/leads/{lead_id}/deliveries` | Create delivery task (background) |
| GET | `/leads/{lead_id}/deliveries` | Delivery task list for lead |
| GET | `/deliveries/{delivery_id}` | Delivery task status |
| POST | `/deliveries/{delivery_id}/retry` | Stepwise retry (text/qr/all) |
| POST | `/leads/_probe-wechat` | WeChat connectivity probe (super-admin only) |

## Contact Packages and Media Assets (联系资料包)

| Method | Path | Description |
|---|---|---|
| GET | `/contact-packages` | List packages |
| GET | `/contact-packages/{pkg_id}` | Package detail |
| POST | `/contact-packages` | Create package |
| PUT | `/contact-packages/{pkg_id}` | Update package |
| POST | `/contact-packages/{pkg_id}/enable` | Enable |
| POST | `/contact-packages/{pkg_id}/disable` | Disable |
| DELETE | `/contact-packages/{pkg_id}` | Soft-delete |
| POST | `/media-assets/upload` | Upload/get WeChat image media_id |
| GET | `/media-assets/{media_id}` | Get WeChat media |
| POST | `/media-assets/{media_id}/refresh` | Force-refresh media |

## WeChat Callback (公开, no auth)

| Method | Path | Description |
|---|---|---|
| GET | `/wechat/callback/{callback_key}` | URL verification (signature check, returns echostr) |
| POST | `/wechat/callback/{callback_key}` | Receive message callback (XML parse + dedup) |

## Watermark Config (租户水印)

| Method | Path | Description |
|---|---|---|
| GET | `/watermark-config` | Get tenant config (creates default if absent) |
| PUT | `/watermark-config` | Update tenant config |
| POST | `/watermark-config/upload-logo` | Upload watermark logo to MinIO |
| POST | `/watermark-config/preview` | Preview watermark rendering (no save) |

## Statistics (统计)

| Method | Path | Description |
|---|---|---|
| GET | `/statistics/dashboard` | Dashboard overview (accounts/active tasks/articles/recent activity) |
| GET | `/statistics/agent-logs` | Agent execution logs (paged, filtered) |
| GET | `/statistics/articles/quality-distribution` | Quality distribution |
| GET | `/statistics/articles/optimization-report` | Optimization effect report |

## Publish Plans (发布计划)

| Method | Path | Description |
|---|---|---|
| GET | `/publish-plans` | List plans (account filter) |
| POST | `/publish-plans` | Create plan |
| PUT | `/publish-plans/{plan_id}` | Update plan |
| DELETE | `/publish-plans/{plan_id}` | Delete plan |

## Error Handling Notes

- `401` → refresh token once, then ask for credentials.
- `403` → permission/tenant boundary; do not try other IDs.
- `404` → resource not in this tenant; re-inventory.
- `422` → validation details in response; fix payload.
- `409` → conflict (duplicate app_id / slug / idempotency key).
- `429` / `5xx` → transient upstream failure; rely on scheduled retry policy.
