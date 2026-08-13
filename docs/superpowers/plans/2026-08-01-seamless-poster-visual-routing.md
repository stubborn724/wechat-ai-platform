# 无缝海报视觉路由与草稿验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变普通文章和已有定时任务的前提下，让显式选择 `seamless_poster` 的任务按 ERP 来源自动使用对应品牌知识库，并生成视觉风格统一、图片之间零间距的海报文章草稿。

**Architecture:** 保留现有逐张独立海报生成方式，新增纯函数路由把 ERP `source_key` 映射到同品牌的文章格式库和背景库。海报提示词增加本篇共享的视觉锚点，所有图片复用同一套背景、色彩、材质、光线和文字留白规则；发布层继续使用独立图片块级零间距排列，不对普通文章图片做任何处理。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、Pydantic、Pillow/图片归档服务、pytest、Vue 3、Celery。

---

### Task 1: 固化 ERP 来源与品牌知识库路由

**Files:**
- Create: `backend/app/services/brand_knowledge_routing.py`
- Modify: `backend/app/services/publication_format_service.py`
- Test: `backend/tests/test_brand_knowledge_routing.py`

- [x] **Step 1: Write the failing test**

覆盖四个已配置 ERP 来源的同品牌文章格式库和背景库名称映射；当任务只绑定背景库时，路由结果必须补齐同品牌格式库；未知来源只保留显式绑定 ID。

- [x] **Step 2: Run the focused test and verify it fails**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_brand_knowledge_routing.py -q`

Expected: FAIL because the routing module and resolver do not exist。

- [x] **Step 3: Implement the minimal routing module**

定义不可变的 `BrandKnowledgeRoute`，集中维护 `xiuman`、`zhongxiwujie`、`xiehuai`、`jianzhi` 对应的文章格式库和背景库名称；提供按来源键查找和按已加载知识库记录补齐 ID 的纯函数。

- [x] **Step 4: Run the focused test and verify it passes**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_brand_knowledge_routing.py -q`

Expected: PASS。

### Task 2: 增加海报共享视觉锚点

**Files:**
- Modify: `backend/app/services/poster_article_service.py`
- Modify: `backend/app/services/publication_format_service.py`
- Test: `backend/tests/test_poster_article_service.py`
- Test: `backend/tests/test_publication_format_service.py`

- [x] **Step 1: Write the failing test**

断言同一 `PublicationFormatProfile` 生成的每一张海报提示词都包含固定的“本篇统一视觉锚点”，并包含知识库中的背景、色彩、材质和文字留白规则；同时保留每张海报独立的场景和文案变化。

- [x] **Step 2: Run the focused test and verify it fails**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_poster_article_service.py -q`

Expected: FAIL because当前提示词没有共享视觉锚点段落。

- [x] **Step 3: Implement the minimal prompt contract**

从 `visual_directives` 生成稳定的共享锚点文本，明确所有图片必须保持同品牌空间体系、色彩、光线、材质、镜头语言和文字安全区，仅允许场景焦点与文案变化；将它注入每一次独立生图请求。

- [x] **Step 4: Run the focused test and verify it passes**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_poster_article_service.py backend/tests/test_publication_format_service.py -q`

Expected: PASS。

### Task 3: 接入定时任务并保持旧链路隔离

**Files:**
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/app/services/publication_format_service.py`
- Modify: `backend/tests/test_scheduled_publication_policy.py`
- Create: `backend/tests/test_scheduled_poster_knowledge_routing.py`

- [x] **Step 1: Write the failing test**

覆盖显式 `seamless_poster` 任务使用 ERP 来源键自动补齐同品牌格式库；覆盖 `standard` 任务不触发路由；覆盖没有匹配背景库时停止发布并返回明确错误。

- [x] **Step 2: Run the focused test and verify it fails**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_poster_knowledge_routing.py backend/tests/test_scheduled_publication_policy.py -q`

Expected: FAIL because执行器当前只读取任务显式绑定的知识库 ID。

- [x] **Step 3: Implement the isolated poster route**

仅在 `layout_mode == "seamless_poster"` 且存在 ERP 来源时查询当前租户启用的知识库，按来源键补齐同品牌格式库和背景库，再交给现有发布格式解析器；`standard` 分支不调用新路由。海报 HTML 继续使用块级图片、零 margin、零 line-height，不改变普通图片归档的固定尺寸策略。

- [x] **Step 4: Run the focused test and verify it passes**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_poster_knowledge_routing.py backend/tests/test_scheduled_publication_policy.py backend/tests/test_poster_article_service.py backend/tests/test_publication_format_service.py -q`

Expected: PASS。

### Task 4: 回归验证与部署数据检查

**Files:**
- Modify: `docs/superpowers/plans/2026-08-01-seamless-poster-visual-routing.md`

- [x] **Step 1: Run backend focused tests**

Run: `& backend/venv/Scripts/python.exe -m pytest backend/tests/test_brand_knowledge_routing.py backend/tests/test_scheduled_poster_knowledge_routing.py backend/tests/test_poster_article_service.py backend/tests/test_publication_format_service.py backend/tests/test_scheduled_publication_policy.py -q`

Expected: all focused tests pass。

- [x] **Step 2: Run the frontend build**

Run: `npm run build` in `frontend`。

Expected: build succeeds；仅允许已有依赖注释和 chunk size 警告。

- [x] **Step 3: Apply the idempotent database migration**

Run: `& backend/venv/Scripts/python.exe backend/scripts/migrate_scheduled_task_layout_mode.py`

Expected: `layout_mode` 已存在或成功添加，历史任务默认 `standard`。

- [x] **Step 4: Inspect the live task and matching knowledge bases**

确认 ERP 来源键、匹配品牌格式库/背景库、公众号账号均属于当前租户，且测试任务发布模式为 `draft`。

### Task 5: 创建临近时间任务并验证微信草稿

**Files:**
- No source changes; use existing MySQL, PostgreSQL, ERP and relay configuration。

- [x] **Step 1: Create an isolated test task**

使用当前租户已配置的 ERP 来源和对应知识库，`layout_mode="seamless_poster"`、`content_type="article"`、`publish_mode="draft"`，只绑定一个公众号账号，并将发布时间设置为当前时间之后的下一个可执行分钟。

- [x] **Step 2: Wait for the scheduled run**

观察 Celery Worker/Beat 日志和 `scheduled_task_runs`、`articles` 状态，确认图片生成、素材归档、草稿提交均完成；失败时先保存错误信息，不重复提交草稿。

- [x] **Step 3: Verify the generated article**

确认正文图片使用 ERP 素材、每张图片的提示词包含对应品牌背景规则、HTML 图片节点连续且无段落空白、微信返回草稿 ID，并把任务 ID、文章 ID、运行 ID 和草稿状态反馈给用户。

验证结果：测试任务 `12` 使用绣蔓 ERP 来源 `xiuman`、公众号账号 `104`、
`seamless_poster + draft`，运行记录 `53` 成功生成文章 `75` 并保存微信草稿；
任务运行时从显式背景库 `10` 自动补齐同品牌格式库 `9`，文章主容器包含 4 张
`1024×1536` 图片，图片节点无段落间距，微信返回草稿 `media_id`。验证任务已停用，
避免后续日期重复生成草稿。
