# ERP 产品与知识库背景路由 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让定时图文任务在使用投喂源文章结构时，仍能以 ERP 产品图为唯一视觉主体，并以知识库规则生成背景；只有非 ERP 的 AI 生图路径才参考投喂源图片。

**Architecture:** 新增无副作用的图片策略模块，将任务配置归一为三种视觉模式：`erp_knowledge_background`、`reference_visual_imitation`、`standard_generation`。定时执行器只读取该模块的决定来加载参考图片和选择图片生成路径，避免“是否有投喂源图片”的隐式条件覆盖 ERP 选择。前端根据相同规则提示用户投喂源、ERP 与知识库各自的职责。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、pytest、Vue 3、TypeScript、Element Plus。

---

## 文件职责

- `backend/app/services/scheduled_image_routing_policy.py`：只解析任务图片配置，输出可测试的图片路径和是否允许视觉参考图。
- `backend/app/tasks/scheduled_task_executor.py`：根据策略加载投喂源文本/结构、ERP 原图及知识库背景，并选择正确的图片生成分支。
- `backend/tests/test_scheduled_image_routing_policy.py`：锁定 ERP + 投喂源 + 知识库时不分析投喂源图片的回归行为。
- `frontend/src/views/ScheduledTasksView.vue`：说明 ERP、投喂源和知识库的独立职责，防止错误配置预期。

### Task 1: 锁定图片来源优先级

**Files:**
- Create: `backend/tests/test_scheduled_image_routing_policy.py`
- Create: `backend/app/services/scheduled_image_routing_policy.py`

- [ ] **Step 1: 写出失败测试，表达 ERP 路径不允许投喂源视觉仿写**

```python
def test_erp_product_with_feed_structure_uses_knowledge_background_only():
    decision = resolve_scheduled_image_route(
        has_erp_product=True,
        has_feed_source=True,
        has_knowledge_base=True,
    )

    assert decision.mode == "erp_knowledge_background"
    assert decision.load_reference_visuals is False
    assert decision.requires_knowledge_background is True
```

- [ ] **Step 2: 运行失败测试，确认失败是路由模块尚不存在**

Run: `./venv/Scripts/python.exe -m pytest tests/test_scheduled_image_routing_policy.py -q`

Expected: FAIL，提示无法导入 `scheduled_image_routing_policy`。

- [ ] **Step 3: 实现不可变策略对象和唯一优先级规则**

```python
@dataclass(frozen=True)
class ScheduledImageRoute:
    mode: Literal["erp_knowledge_background", "reference_visual_imitation", "standard_generation"]
    load_reference_visuals: bool
    requires_knowledge_background: bool

def resolve_scheduled_image_route(*, has_erp_product: bool, has_feed_source: bool, has_knowledge_base: bool) -> ScheduledImageRoute:
    if has_erp_product:
        return ScheduledImageRoute("erp_knowledge_background", False, True)
    if has_feed_source:
        return ScheduledImageRoute("reference_visual_imitation", True, False)
    return ScheduledImageRoute("standard_generation", False, False)
```

- [ ] **Step 4: 运行策略测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/test_scheduled_image_routing_policy.py -q`

Expected: PASS；覆盖 ERP + 投喂源 + 知识库、单独投喂源与无来源三个分支。

### Task 2: 将执行器改为按图片策略取数和生成

**Files:**
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_scheduled_image_routing_policy.py`

- [ ] **Step 1: 添加失败测试，ERP 路径不会把投喂源图片传给视觉仿写函数**

```python
def test_erp_route_does_not_pass_feed_image_urls_to_reference_imitation():
    route = resolve_scheduled_image_route(
        has_erp_product=True,
        has_feed_source=True,
        has_knowledge_base=True,
    )
    assert route.load_reference_visuals is False
```

- [ ] **Step 2: 在执行器创建状态前解析路由，并只在允许时收集 `ref_image_urls`**

```python
image_route = resolve_scheduled_image_route(
    has_erp_product=erp_image_config is not None,
    has_feed_source=has_feed_source,
    has_knowledge_base=bool(task.knowledge_base_ids),
)
ref_image_urls = []
# 投喂源始终加载 HTML、文章文本与风格；仅 AI 视觉仿写模式收集其图片 URL。
if image_route.load_reference_visuals:
    ref_image_urls.extend(extract_markdown_image_urls(body))
```

- [ ] **Step 3: 以路由模式替代图片分支的隐式条件**

```python
if image_route.mode == "reference_visual_imitation":
    await _gen_images_from_references(s, ref_image_urls)
else:
    # ERP 模式的 state 已有 ERP 原图字节与知识库背景；统一 Agent 5 走图生图。
    s = await agent4_analyze_image_requirements(s)
    s = await agent5_generate_images(s)
```

- [ ] **Step 4: 运行回归测试**

Run: `./venv/Scripts/python.exe -m pytest tests/test_scheduled_image_routing_policy.py tests/test_article_image_provider_routing.py tests/test_scheduled_article_context_service.py -q`

Expected: PASS；ERP 请求带参考图片和知识库提示词，投喂源图片不参与该路径。

### Task 3: 明确前端配置语义

**Files:**
- Modify: `frontend/src/views/ScheduledTasksView.vue`

- [ ] **Step 1: 将正文配图提示改为来源优先级说明**

```html
<span class="form-hint">
  选择 ERP 产品库时：投喂源仅决定文章结构和文案风格；ERP 产品图作为主体，所选知识库决定生成背景。只有未选择 ERP 时，AI 生图才会参考投喂源图片风格。
</span>
```

- [ ] **Step 2: 构建前端**

Run: `npm run build`

Expected: exit code 0，TypeScript 与 Vite 构建通过。

### Task 4: 完整验证

**Files:**
- Modify: `backend/app/services/scheduled_image_routing_policy.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_scheduled_image_routing_policy.py`
- Modify: `frontend/src/views/ScheduledTasksView.vue`

- [ ] **Step 1: 运行相关后端测试**

Run: `./venv/Scripts/python.exe -m pytest tests/test_scheduled_image_routing_policy.py tests/test_article_image_provider_routing.py tests/test_scheduled_article_context_service.py tests/test_image_generation_entrypoints.py -q`

Expected: PASS。

- [ ] **Step 2: 检查变更质量**

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 3: 提交本次改动**

```powershell
git add -- backend/app/services/scheduled_image_routing_policy.py backend/app/tasks/scheduled_task_executor.py backend/tests/test_scheduled_image_routing_policy.py frontend/src/views/ScheduledTasksView.vue docs/superpowers/plans/2026-07-30-erp-knowledge-background-routing.md
git commit -m '明确ERP产品知识库背景生成规则'
```
