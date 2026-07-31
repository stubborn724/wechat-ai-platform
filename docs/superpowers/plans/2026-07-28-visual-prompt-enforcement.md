# Visual Prompt Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将视觉分析的结构化结果强制合成为高相似度生图提示词，避免图文和纯图片仿写退化为关键词生图。

**Architecture:** 在 `reference_image_imitation_service` 中提供纯函数提示词合成器，接收视觉描述、新主体和可选补充描述，统一输出万相最终提示词。HTML 图文流程在内容 Agent 返回图片槽位后立刻合成最终提示词；纯图片和定时图文流程复用同一函数；遗留 LangGraph 节点修复为始终传递已生成的 `ImageRequirement.prompt`。

**Tech Stack:** Python 3.11、pytest、Pydantic、LangChain/DashScope、通义万相。

---

## 文件职责

- `backend/app/services/reference_image_imitation_service.py`：视觉约束提示词合成器及纯图片共享编排。
- `backend/app/services/article_agent_service.py`：HTML 图片槽位将视觉描述与新主体合成为最终提示词。
- `backend/app/tasks/scheduled_task_executor.py`：定时图文和纯图片调用高相似度共享合成器。
- `backend/app/agent/nodes/image_generation_node.py`：遗留 LangGraph 节点异步调用图片策略并透传 `ImageRequirement.prompt`。
- `backend/tests/test_reference_image_imitation_service.py`：验证强制视觉字段、高相似度与排除约束。
- `backend/tests/test_html_imitation_service.py`：验证 HTML 图片需求保留合成后的最终提示词。
- `backend/tests/test_image_generation_node.py`：验证遗留节点使用 `prompt` 而不是只用关键词。

### Task 1: 锁定视觉约束提示词的失败测试

**Files:**
- Modify: `backend/tests/test_reference_image_imitation_service.py`
- Modify: `backend/tests/test_html_imitation_service.py`
- Create: `backend/tests/test_image_generation_node.py`

- [ ] **Step 1: 写出合成器必须保留视觉字段和排除要求的测试**

```python
def test_compose_visual_imitation_prompt_keeps_visual_constraints_and_replaces_subject():
    prompt = compose_visual_imitation_prompt(
        {"subject": "旧主体", "composition": "居中对称", "camera": "低机位广角",
         "lighting": "侧逆光", "color_palette": "青橙色调", "visual_style": "电影海报",
         "details": ["雨水反光"], "mood": "紧张"},
        subject="新主体",
        supplement="画面层次丰富",
    )
    assert "新主体" in prompt
    assert "居中对称" in prompt
    assert "低机位广角" in prompt
    assert "侧逆光" in prompt
    assert "青橙色调" in prompt
    assert "电影海报" in prompt
    assert "不要包含任何文字、品牌、水印、签名、标签或二维码" in prompt
```

- [ ] **Step 2: 写出 HTML 槽位忽略空 Agent 提示词的失败测试**

```python
assert result.image_requirements[0].prompt
assert "雨后街道" in result.image_requirements[0].prompt
assert "电影感" in result.image_requirements[0].prompt
```

- [ ] **Step 3: 写出遗留 LangGraph 节点透传完整提示词的失败测试**

```python
def test_resolve_image_single_passes_requirement_prompt_to_strategy(monkeypatch):
    monkeypatch.setattr(image_generation_node, "ImageServiceStrategy", FakeStrategy)
    result = image_generation_node._resolve_image_single(requirement.model_dump(), "DASHSCOPE")
    assert FakeStrategy.received_prompt == "结构化视觉提示词"
```

- [ ] **Step 4: 运行失败测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_reference_image_imitation_service.py tests/test_html_imitation_service.py tests/test_image_generation_node.py -q`

Expected: `compose_visual_imitation_prompt` 不存在，HTML 提示词断言失败，遗留节点未能接收完整提示词。

### Task 2: 实现高相似度视觉提示词合成器

**Files:**
- Modify: `backend/app/services/reference_image_imitation_service.py`
- Test: `backend/tests/test_reference_image_imitation_service.py`

- [ ] **Step 1: 定义纯函数接口**

```python
def compose_visual_imitation_prompt(
    visual_description: Mapping[str, object],
    *,
    subject: str,
    supplement: str = "",
) -> str:
    ...
```

- [ ] **Step 2: 以固定顺序合成高相似度视觉约束**

```python
parts = [
    f"主体：{subject}",
    f"场景：{visual_description.get('scene', '')}",
    f"构图与版式：{visual_description.get('composition', '')}",
    f"镜头：{visual_description.get('camera', '')}",
    f"光影：{visual_description.get('lighting', '')}",
    f"色调：{visual_description.get('color_palette', '')}",
    f"视觉风格：{visual_description.get('visual_style', '')}",
]
```

过滤空字段，追加最多三个 `details`、情绪和补充描述；补充描述不能替代上述字段。

- [ ] **Step 3: 追加固定排除约束并接入共享图片编排**

```python
final_prompt = "，".join(parts)
return f"{final_prompt}。高相似度还原参考图的构图、镜头、光影、色调与版式风格。不要包含任何文字、品牌、水印、签名、标签或二维码。"
```

将 `build_reference_image_prompt` 改为以 `similarity="high"` 调用提示词 Agent，并把其输出作为 `supplement` 传入合成器。

- [ ] **Step 4: 运行服务测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_reference_image_imitation_service.py -q`

Expected: PASS。

### Task 3: 在 HTML 图文和定时图文流程强制使用合成器

**Files:**
- Modify: `backend/app/services/article_agent_service.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_html_imitation_service.py`

- [ ] **Step 1: 在 HTML 内容 Agent 解析槽位后重写图片提示词**

```python
image_by_slot = {
    slot_id: {
        "keywords": image_data["keywords"],
        "prompt": compose_visual_imitation_prompt(
            visual_descriptions.get(slot_id, {}),
            subject=image_data["keywords"],
            supplement=image_data["prompt"],
        ),
    }
    for slot_id, image_data in image_by_slot.items()
}
```

- [ ] **Step 2: 保持二维码排除与缺失视觉描述降级行为**

二维码排除槽位不得参与上述字典构造。没有视觉描述时仍使用新主体和补充描述，但必须保留固定的无文字、无品牌、无水印、无二维码限制。

- [ ] **Step 3: 确保定时图文参考图流程使用高相似度合成器**

保留 `_gen_images_from_references` 的正文占位符循环；其每次生成均调用已更新的 `build_reference_image_prompt`，不得自行直接调用 `craft_prompt` 或 `build_wanxiang_prompt`。

- [ ] **Step 4: 运行 HTML 回归测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_html_imitation_service.py tests/test_reference_image_imitation_service.py -q`

Expected: PASS。

### Task 4: 修复遗留 LangGraph 图片节点的提示词丢失

**Files:**
- Modify: `backend/app/agent/nodes/image_generation_node.py`
- Create: `backend/tests/test_image_generation_node.py`

- [ ] **Step 1: 直接导入生产图片策略并规范化方法名**

```python
from app.services.image_service_v2 import ImageServiceStrategy

method = (requirement.image_source or image_source or "DASHSCOPE").upper()
```

- [ ] **Step 2: 在同步节点中安全执行异步图片策略并传递提示词**

```python
strategy = ImageServiceStrategy()
url = asyncio.run(
    strategy.execute(method, requirement.keywords, prompt=requirement.prompt)
)
```

节点运行于线程池工作线程；异常时保留已有的降级逻辑。删除只传 `requirement.keywords` 的万相调用。

- [ ] **Step 3: 运行遗留节点测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_image_generation_node.py -q`

Expected: PASS，策略收到 `ImageRequirement.prompt`。

### Task 5: 完成验证

**Files:**
- Modify: `backend/app/services/reference_image_imitation_service.py`
- Modify: `backend/app/services/article_agent_service.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/app/agent/nodes/image_generation_node.py`
- Modify: `backend/tests/test_reference_image_imitation_service.py`
- Modify: `backend/tests/test_html_imitation_service.py`
- Create: `backend/tests/test_image_generation_node.py`

- [ ] **Step 1: 运行视觉仿写聚焦测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_reference_image_imitation_service.py tests/test_reference_media_analysis_service.py tests/test_html_imitation_service.py tests/test_image_generation_node.py -q`

Expected: PASS。

- [ ] **Step 2: 编译修改模块并检查空白**

Run: `& 'venv/Scripts/python.exe' -m py_compile app/services/reference_image_imitation_service.py app/services/article_agent_service.py app/tasks/scheduled_task_executor.py app/agent/nodes/image_generation_node.py; git diff --check`

Expected: 命令退出码为 0。
