# 通用投喂源仿写闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户只需把公众号文章链接导入投喂源，系统自动识别 HTML 槽位或无缝海报格式，并让新建定时任务自动绑定对应模板完成仿写；已上线的历史 ERP 任务保持原有行为。

**Architecture:** 程序化格式分析器负责从源 HTML 生成结构化模板，版本化持久化服务负责幂等复用和版本递增，任务绑定服务负责按文章/投喂源选择模板。执行器仍以持久化 `format_profile_id` 为唯一新管线开关；历史任务另有 `format_profile_auto_bind_enabled=0` 保护，新任务由 API 明确写入 `1`。

**Tech Stack:** FastAPI, SQLAlchemy/MySQL, Pydantic, Vue 3, pytest, Celery。

---

### Task 1: 格式分析结果版本化

**Files:**
- Create: `backend/app/services/format_profile_persistence_service.py`
- Modify: `backend/app/api/v1/feed_sources.py`
- Test: `backend/tests/test_format_profile_persistence_service.py`

- [x] 用纯单元测试锁定同一 HTML 复用模板、版式变化创建下一版本、空 HTML 拒绝三种行为。
- [x] 通过 SHA-256 源 HTML 指纹和结构快照比较实现幂等持久化，不把原 HTML 重复发送给模型。
- [x] 将手动格式分析 API 改为复用统一持久化服务。
- [x] 验证：`pytest tests/test_format_profile_persistence_service.py tests/test_format_profile_service.py -q`。

### Task 2: 链接导入自动分析

**Files:**
- Modify: `backend/app/services/feed_service.py`
- Modify: `backend/app/api/v1/feed_sources.py`
- Modify: `frontend/src/views/FeedSourcesView.vue`
- Modify: `frontend/src/api/types.ts`
- Test: `backend/tests/test_feed_source_auto_format_profile.py`

- [x] 每篇文章入库后自动创建或复用格式模板；单篇格式异常只返回警告，不回滚整次抓取。
- [x] 新建 URL、公众号或 RSS 投喂源后自动执行首次抓取和格式分析。
- [x] 抓取结果返回模板创建数量和格式错误明细，前端展示闭环状态。
- [x] 文章列表区分“格式已分析”和“风格已分析”，不再把两个生命周期混为一个 `is_analyzed` 状态。
- [x] 保留“重新分析格式”作为源版式变化后的高级入口。

### Task 3: 定时任务自动绑定与历史隔离

**Files:**
- Create: `backend/app/services/format_profile_task_binding_service.py`
- Modify: `backend/app/models/mysql_models.py`
- Modify: `backend/app/api/v1/scheduled_tasks.py`
- Modify: `backend/app/services/format_profile_task_policy.py`
- Modify: `backend/scripts/migrate_article_format_profiles.py`
- Test: `backend/tests/test_format_profile_auto_binding.py`

- [x] 优先使用任务明确选择的文章模板；未选择文章时使用投喂源最新文章的最新模板。
- [x] 新建任务不需要手动选模板，服务端自动保存 `format_profile_id`。
- [x] 已绑定任务保存其他字段时保持模板版本锁定；切换投喂源时才重新匹配。
- [x] 新增历史任务保护开关：迁移把已有任务设为关闭，新建任务设为开启，确保“绣蔓仿写”不被自动切换。
- [x] 本地已执行幂等迁移，确认 `绣蔓仿写` 的 `format_profile_id` 为空且自动绑定开关为 `0`。
- [x] 验证：`pytest tests/test_format_profile_auto_binding.py tests/test_format_profile_task_binding.py -q`。

### Task 4: 前端默认自动模式

**Files:**
- Modify: `frontend/src/views/FeedSourcesView.vue`
- Modify: `frontend/src/views/ScheduledTasksView.vue`
- Modify: `frontend/src/api/types.ts`
- Test: `backend/tests/test_format_profile_ui_contract.py`

- [x] 投喂源页面说明“导入后自动分析格式”。
- [x] 定时任务页面将模板选择改为“格式模板覆盖（可选）”。
- [x] 切换投喂源时清空旧模板 ID，交给后端重新自动绑定。
- [x] 任务列表显示绑定的模板名称和版本；没有绑定时明确显示历史流程。
- [x] 验证：`npm run build` 和 UI 契约测试通过。

### Task 5: 回归验证

**Files:**
- Create: `backend/tests/test_unified_feed_imitation_contract.py`

- [x] 验证投喂抓取、模板持久化、任务绑定和执行器白名单已经连成一条链路。
- [x] 格式闭环及相关调度测试通过：22 项闭环测试、27 项调度/水印/海报测试通过。
- [x] `compileall` 和 `git diff --check` 通过。
- [x] 前端构建通过；Vite 仅报告既有 chunk size 和第三方 PURE 注释警告。
- [x] 全量测试结果记录：254 passed；本地测试库另有 38 项因既有 `tageai_integration_invocations` 缺表产生错误，另有 1 个与本功能无关的万相诊断测试失败。
