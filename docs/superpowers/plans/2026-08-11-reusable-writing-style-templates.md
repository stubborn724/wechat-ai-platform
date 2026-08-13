# 可复用写作风格模板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让定时任务通过简单下拉选择可复用写作风格模板，并将她格公域、私域任务绑定到统一的企业 AI 服务模板。

**Architecture:** 保持 `scheduled_tasks.style` 作为兼容存储字段，但把其值规范为稳定的模板编号。新增后端模板目录服务，作为名称、说明和生成提示词的唯一来源；API 暴露只读选项，前端动态加载并显示说明。未选择模板和历史风格编号继续沿用现有生成行为。

**Tech Stack:** FastAPI、SQLAlchemy、Pydantic、Vue 3、Element Plus、pytest、Docker Compose。

---

### Task 1: 建立后端写作风格模板目录

**Files:**
- Create: `backend/app/services/writing_style_template_service.py`
- Create: `backend/tests/test_writing_style_template_service.py`
- Modify: `backend/app/constants/prompt.py`

- [ ] **Step 1: 编写她格模板解析失败测试**

```python
def test_shege_template_exposes_operator_facing_metadata_and_prompt():
    template = get_writing_style_template("shege_enterprise_ai_service")

    assert template is not None
    assert template.label == "她格 - 企业 AI 服务"
    assert "经营问题" in template.description
    assert "标题" in template.prompt
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_writing_style_template_service.py -q'`

Expected: FAIL，因为模板目录服务尚不存在。

- [ ] **Step 3: 实现不可编辑的内置模板目录和提示词解析**

```python
@dataclass(frozen=True)
class WritingStyleTemplate:
    identifier: str
    label: str
    description: str
    prompt: str

def list_writing_style_templates() -> tuple[WritingStyleTemplate, ...]:
    return _WRITING_STYLE_TEMPLATES

def get_writing_style_template(identifier: str | None) -> WritingStyleTemplate | None:
    return _TEMPLATE_BY_IDENTIFIER.get((identifier or "").strip().lower())
```

并让 `get_style_prompt` 优先从目录服务读取模板提示词，再回退到现有科技、情感、教育、幽默风格映射。

- [ ] **Step 4: 运行模板与既有提示词测试**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_writing_style_template_service.py -q'`

Expected: PASS。

### Task 2: 为定时任务提供模板选择 API

**Files:**
- Modify: `backend/app/api/v1/scheduled_tasks.py`
- Modify: `backend/tests/test_writing_style_template_service.py`

- [ ] **Step 1: 编写 API 返回只读模板选项的失败测试**

```python
def test_writing_style_template_response_contains_shege_option():
    options = list_writing_style_template_options()

    assert any(item.identifier == "shege_enterprise_ai_service" for item in options)
    assert all(item.prompt is None for item in options)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_writing_style_template_service.py -q'`

Expected: FAIL，因为 API 选项模型和转换函数尚不存在。

- [ ] **Step 3: 新增只读选项模型与 GET 接口**

```python
class WritingStyleTemplateOption(BaseModel):
    identifier: str
    label: str
    description: str
    prompt: None = None

@router.get("/scheduled-tasks/writing-style-templates")
def list_writing_style_templates():
    return [to_option(item) for item in list_writing_style_templates()]
```

接口只返回模板编号、名称、说明，不返回内部提示词。保存任务时继续写入既有 `style` 字段，避免数据库迁移和历史任务风险。

- [ ] **Step 4: 运行 API 选项测试**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_writing_style_template_service.py -q'`

Expected: PASS。

### Task 3: 更新定时任务表单为模板选择

**Files:**
- Modify: `frontend/src/views/ScheduledTasksView.vue`
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: 定义前端模板选项类型和加载函数**

```typescript
interface WritingStyleTemplateOption {
  identifier: string
  label: string
  description: string
}

async function loadWritingStyleTemplates() {
  const { data } = await client.get<WritingStyleTemplateOption[]>(
    '/scheduled-tasks/writing-style-templates',
  )
  writingStyleTemplates.value = data
}
```

- [ ] **Step 2: 将“写作风格”控件替换为“写作模板”控件**

```vue
<el-form-item label="写作模板">
  <el-select v-model="form.style" clearable placeholder="自动匹配内容来源">
    <el-option label="自动匹配" value="" />
    <el-option v-for="option in writingStyleTemplates" :key="option.identifier"
      :label="option.label" :value="option.identifier" />
  </el-select>
  <span v-if="selectedWritingStyleTemplate" class="form-hint">
    {{ selectedWritingStyleTemplate.description }}
  </span>
</el-form-item>
```

旧任务保存时如果风格值不在目录中，显示“历史任务风格”，并原样提交，避免运营编辑时间或账号时意外清空历史配置。

- [ ] **Step 3: 构建前端**

Run: `npm run build`

Expected: exit code 0。

### Task 4: 将她格任务迁移到公共模板并回归核验

**Files:**
- Modify: `backend/scripts/initialize_shege_original_tasks.py`
- Modify: `backend/tests/test_initialize_shege_original_tasks.py`

- [ ] **Step 1: 编写她格任务保存模板编号的失败测试**

```python
assert specification["style"] == "shege_enterprise_ai_service"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_initialize_shege_original_tasks.py -q'`

Expected: FAIL，因为任务仍保存原始文风文本。

- [ ] **Step 3: 修改初始化器并执行幂等更新**

```python
"style": SHEGE_WRITING_STYLE_TEMPLATE_ID,
```

运行：`docker compose exec celery-worker python scripts/initialize_shege_original_tasks.py`。脚本仅更新精确名称为她格原创公域、私域的任务。

- [ ] **Step 4: 执行最终测试、构建和实际配置查询**

Run: `docker compose exec celery-worker sh -lc 'python -m pytest tests/test_writing_style_template_service.py tests/test_initialize_shege_original_tasks.py tests/test_footer_template_service.py -q'`

Run: `npm run build`

Expected: 测试全绿、前端构建通过；她格两任务的 `style` 均为 `shege_enterprise_ai_service`，其余定时任务不被写入。
