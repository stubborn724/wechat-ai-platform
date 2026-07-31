# Reference Image Imitation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一图文与纯图片仿写的参考图片分析，跳过二维码并保留非二维码图片的顺序仿写，同时清理标题末尾无语义标点。

**Architecture:** `reference_media_analysis_service` 保持纯函数性质，输出可用图片及二维码的原始索引。新增 `reference_image_imitation_service` 负责使用视觉描述构建提示词、调用图片生成和素材归档；HTTP 与定时入口仅加载参考文章、调用该服务并持久化结果。HTML 流程使用二维码索引排除 DOM 图片槽位，防止二维码被送入内容 Agent 或图片生成队列。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、pytest、BeautifulSoup、LangChain/DashScope。

---

## 文件职责

- `backend/app/services/reference_media_analysis_service.py`：提取参考图片、绑定视觉理解结果、标识二维码原始索引。
- `backend/app/services/reference_image_imitation_service.py`：协调单张非二维码图片的提示词构建、万相生成和可选素材归档。
- `backend/app/services/article_agent_service.py`：将二维码原始索引转换为 HTML 图片槽位排除集。
- `backend/app/api/v1/articles.py`：即时纯图片入口复用图片仿写服务。
- `backend/app/tasks/scheduled_task_executor.py`：定时纯图片入口复用同一服务。
- `backend/tests/test_reference_image_imitation_service.py`：验证共享编排、二维码跳过和异常继续策略。
- `backend/tests/test_html_imitation_service.py`：验证二维码 HTML 槽位不会进入内容 Agent 或最终 DOM。

### Task 1: 锁定二维码和标题回归行为

**Files:**
- Modify: `backend/tests/test_html_imitation_service.py`
- Create: `backend/tests/test_reference_image_imitation_service.py`

- [ ] **Step 1: 写出 HTML 二维码排除的失败测试**

```python
def test_html_content_agent_excludes_image_slots_identified_as_qrcodes(monkeypatch):
    monkeypatch.setattr(image_understanding_node, "understand_images", lambda _: [{"is_qrcode": True}])
    result = asyncio.run(agent3_generate_html_imitation_content(state))
    assert '"image_slots": []' in captured_prompt
    assert "<img" not in result.content
    assert result.image_requirements == []
```

- [ ] **Step 2: 写出共享纯图片编排的失败测试**

```python
def test_imitate_reference_images_skips_qrcode_and_preserves_non_qrcode_order():
    result = asyncio.run(imitate_reference_images(
        ["first", "qr", "last"], "新主题", tenant_id=1,
        understand_images_fn=lambda _: [
            {"subject": "第一张", "is_qrcode": False},
            {"subject": "二维码", "is_qrcode": True},
            {"subject": "最后一张", "is_qrcode": False},
        ],
        craft_prompt_fn=lambda desc, **_: {"prompt": desc["subject"]},
        fallback_prompt_fn=lambda *args: "fallback",
        generate_image_fn=fake_generate_image,
        archive_image_fn=fake_archive_image,
    ))
    assert result.generated_urls == ("generated-first", "generated-last")
    assert result.skipped_qrcode_count == 1
    assert generated_prompts == ["第一张", "最后一张"]
```

- [ ] **Step 3: 运行失败测试并确认失败原因是缺少共享编排与槽位排除**

Run: `pytest backend/tests/test_html_imitation_service.py backend/tests/test_reference_image_imitation_service.py -q`

Expected: HTML 二维码测试仍保留图片槽位，且新服务测试因 `reference_image_imitation_service` 或 `imitate_reference_images` 不存在而失败。

### Task 2: 实现可复用的纯图片仿写编排服务

**Files:**
- Create: `backend/app/services/reference_image_imitation_service.py`
- Test: `backend/tests/test_reference_image_imitation_service.py`

- [ ] **Step 1: 定义不可变的处理结果和显式依赖接口**

```python
@dataclass(frozen=True)
class ReferenceImageImitationResult:
    generated_urls: tuple[str, ...]
    skipped_qrcode_count: int
    skipped_invalid_count: int

async def imitate_reference_images(
    image_urls: Sequence[str], topic: str, *, tenant_id: int,
    understand_images_fn: Callable[[list[str]], list[dict]],
    craft_prompt_fn: Callable[..., dict],
    fallback_prompt_fn: Callable[[dict, str, str], str],
    generate_image_fn: Callable[..., Awaitable[str | None]],
    archive_image_fn: Callable[..., Awaitable[object]],
) -> ReferenceImageImitationResult:
    ...
```

- [ ] **Step 2: 用 `analyze_reference_images` 作为唯一图片识别与二维码过滤来源**

```python
analysis = analyze_reference_images(image_urls, understand_images_fn)
for reference_image in analysis.usable_images:
    description = reference_image.description
    prompt_data = craft_prompt_fn(description, topic=topic, similarity="medium")
    prompt = str(prompt_data.get("prompt", "")).strip()
    if not prompt:
        prompt = fallback_prompt_fn(description, topic, "medium")
    image_url = await generate_image_fn(prompt, size="1024*1365")
    if image_url:
        await archive_image_fn(tenant_id, image_url, keywords=topic[:50])
        generated_urls.append(image_url)
```

- [ ] **Step 3: 处理单张失败并保持其余图片继续生成**

```python
try:
    prompt_data = craft_prompt_fn(description, topic=topic, similarity="medium")
except Exception:
    prompt_data = {"prompt": ""}
```

对每一张图片分别捕获提示词、生成和归档异常；失败只计数并继续下一张。所有可用图片处理完后返回 URL 元组及二维码和无效图片数量。

- [ ] **Step 4: 运行服务测试确认通过**

Run: `pytest backend/tests/test_reference_media_analysis_service.py backend/tests/test_reference_image_imitation_service.py -q`

Expected: PASS，混合图片只生成两张非二维码图片；全部二维码时生成函数调用次数为零。

### Task 3: 将 HTML 图文仿写接入统一过滤结果

**Files:**
- Modify: `backend/app/services/article_agent_service.py`
- Modify: `backend/tests/test_html_imitation_service.py`

- [ ] **Step 1: 将图片理解函数改为返回描述映射和排除槽位集合**

```python
analysis = await asyncio.to_thread(
    analyze_reference_images,
    [slot.source_url for slot in slots_with_urls],
    understand_images,
)
excluded_slot_ids = {
    slots_with_urls[index].slot_id
    for index in analysis.skipped_qrcode_source_indexes
}
visual_descriptions = {
    slots_with_urls[item.source_index].slot_id: item.description
    for item in analysis.usable_images
}
```

- [ ] **Step 2: 将排除集同时传给 Prompt 与 DOM 渲染**

```python
prompt_payload = blueprint.prompt_payload(excluded_image_slot_ids=excluded_slot_ids)
prompt = _build_html_imitation_prompt(state, prompt_payload, visual_descriptions)
rendered = render_html_imitation(
    blueprint,
    text_by_slot=text_by_slot,
    image_by_slot=image_by_slot,
    excluded_image_slot_ids=excluded_slot_ids,
)
```

- [ ] **Step 3: 运行 HTML 流程测试**

Run: `pytest backend/tests/test_html_imitation_service.py -q`

Expected: PASS，标题的 `，` 被渲染层删除；二维码既不在内容 Agent 的 `image_slots` 中，也不在最终 HTML 和图片需求中。

### Task 4: 用共享服务替换两个纯图片入口的重复 Agent 编排

**Files:**
- Modify: `backend/app/api/v1/articles.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Test: `backend/tests/test_reference_image_imitation_service.py`

- [ ] **Step 1: 在 HTTP 入口构造实际依赖并调用共享服务**

```python
wanxiang = WanxiangImageService()
result = await imitate_reference_images(
    extract_markdown_image_urls(ref.body_markdown or ""), new_title,
    tenant_id=principal.tenant_id,
    understand_images_fn=understand_images,
    craft_prompt_fn=craft_prompt,
    fallback_prompt_fn=build_wanxiang_prompt,
    generate_image_fn=wanxiang.generate_image,
    archive_image_fn=lambda tenant_id, url, **kwargs: save_image_to_asset_library(db, tenant_id, url, **kwargs),
)
gen_urls = list(result.generated_urls)
```

- [ ] **Step 2: 在定时入口以同样的依赖调用共享服务**

```python
result = asyncio.run(imitate_reference_images(
    extract_markdown_image_urls(ref.body_markdown or ""), new_title,
    tenant_id=task.tenant_id,
    understand_images_fn=understand_images,
    craft_prompt_fn=craft_prompt,
    fallback_prompt_fn=build_wanxiang_prompt,
    generate_image_fn=WanxiangImageService().generate_image,
    archive_image_fn=lambda tenant_id, url, **kwargs: save_image_to_asset_library(db, tenant_id, url, **kwargs),
))
image_urls = list(result.generated_urls)
```

- [ ] **Step 3: 删除入口内的二维码过滤、提示词回退和逐张生成循环**

入口保留标题产生、文章保存和微信发布。服务返回空 URL 集合时记录“无可仿写的非二维码图片”，并停止发布，不创建空文章。

- [ ] **Step 4: 运行聚焦回归测试**

Run: `pytest backend/tests/test_reference_media_analysis_service.py backend/tests/test_reference_image_imitation_service.py backend/tests/test_html_imitation_service.py -q`

Expected: PASS。

### Task 5: 执行完整验证并提交

**Files:**
- Modify: `backend/app/services/reference_media_analysis_service.py`
- Create: `backend/app/services/reference_image_imitation_service.py`
- Modify: `backend/app/services/article_agent_service.py`
- Modify: `backend/app/api/v1/articles.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_reference_image_imitation_service.py`
- Modify: `backend/tests/test_html_imitation_service.py`

- [ ] **Step 1: 运行全量后端测试**

Run: `pytest backend/tests -q`

Expected: PASS；若出现已有失败，记录与本次改动无关的测试名称和错误文本。

- [ ] **Step 2: 检查变更质量**

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 3: 提交本次功能改动**

```powershell
git add -- backend/app/services/reference_media_analysis_service.py backend/app/services/reference_image_imitation_service.py backend/app/services/article_agent_service.py backend/app/api/v1/articles.py backend/app/tasks/scheduled_task_executor.py backend/tests/test_reference_image_imitation_service.py backend/tests/test_html_imitation_service.py
git commit -m '统一图片仿写二维码过滤'
```
