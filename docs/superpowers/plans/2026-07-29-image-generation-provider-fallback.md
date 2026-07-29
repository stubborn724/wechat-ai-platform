# 图片生成双提供商降级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `gpt-image-2` 接入为所有 AI 图片入口的主提供商，并在临时性故障时自动降级到通义万相。

**Architecture:** 新增提供商协议、中转站适配器和统一主备路由。中转站适配器负责 OpenAI 兼容请求和 Base64 结果归档；万相适配器复用现有服务；业务层只依赖统一图片生成服务。ERP 图生图向主提供商上传参考图字节，降级时继续使用 COS 签名 URL。

**Tech Stack:** Python 3.12、FastAPI、httpx、Pydantic Settings、MinIO、Celery、pytest

---

### Task 1: 图片生成配置与领域协议

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Create: `backend/app/services/image_generation_models.py`
- Test: `backend/tests/test_image_generation_provider_models.py`

- [ ] **Step 1: 写配置和错误分类失败测试**

```python
def test_temporary_provider_error_is_fallback_eligible():
    error = ImageProviderError("timeout", category=ImageErrorCategory.TEMPORARY)
    assert error.can_fallback is True


def test_auth_provider_error_is_not_fallback_eligible():
    error = ImageProviderError("unauthorized", category=ImageErrorCategory.AUTHENTICATION)
    assert error.can_fallback is False
```

- [ ] **Step 2: 运行测试并确认因模块缺失失败**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_provider_models.py -q`

Expected: FAIL，提示 `image_generation_models` 不存在。

- [ ] **Step 3: 实现配置与领域对象**

在 `Settings` 增加：

```python
image_generation_provider: str = "wanxiang"
image_generation_base_url: str = ""
image_generation_api_key: str = ""
image_generation_model: str = "gpt-image-2"
image_generation_edit_model: str = "gpt-image-2"
image_generation_timeout_seconds: int = 240
image_generation_fallback_provider: str = "wanxiang"
image_generation_max_response_bytes: int = 20 * 1024 * 1024
```

领域模块定义 `ImageGenerationRequest`、`GeneratedImage`、`ImageErrorCategory`、`ImageProviderError` 和 `ImageGenerationProvider` 协议。请求对象同时支持 `reference_image_bytes`、`reference_content_type` 和 `reference_image_url`。

- [ ] **Step 4: 运行测试确认通过**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_provider_models.py -q`

Expected: PASS。

### Task 2: 中转站 OpenAI 图片适配器

**Files:**
- Create: `backend/app/services/openai_compatible_image_provider.py`
- Test: `backend/tests/test_openai_compatible_image_provider.py`

- [ ] **Step 1: 写请求协议失败测试**

覆盖以下行为：

```python
@pytest.mark.asyncio
async def test_text_generation_posts_model_and_prompt():
    result = await provider.generate(ImageGenerationRequest(prompt="完整提示词", size="1024*1365"))
    assert captured.url.endswith("/images/generations")
    assert captured.json["model"] == "gpt-image-2"
    assert captured.json["prompt"] == "完整提示词"


@pytest.mark.asyncio
async def test_reference_edit_uploads_image_bytes():
    request = ImageGenerationRequest(
        prompt="只换背景",
        reference_image_bytes=b"image-bytes",
        reference_content_type="image/png",
    )
    await provider.generate(request)
    assert captured.url.endswith("/images/edits")
    assert captured.files["image"][1] == b"image-bytes"
```

- [ ] **Step 2: 运行测试确认缺少适配器而失败**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_openai_compatible_image_provider.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现文本生图、图生图和尺寸转换**

适配器使用 `httpx.AsyncClient(http2=False)`：

```python
if request.reference_image_bytes:
    response = await client.post(
        f"{base_url}/images/edits",
        data={"model": edit_model, "prompt": request.prompt, "size": normalized_size, "n": "1"},
        files={"image": ("reference.png", request.reference_image_bytes, request.reference_content_type)},
    )
else:
    response = await client.post(
        f"{base_url}/images/generations",
        json={"model": model, "prompt": request.prompt, "size": normalized_size, "n": 1},
    )
```

`1024*1365` 映射到提供商支持的最接近尺寸；日志记录原尺寸与映射尺寸。

- [ ] **Step 4: 写并运行结果规范化失败测试**

测试 `data[*].url` 数据 URI、`data[*].b64_json` 和 HTTPS URL。Base64 超限、MIME 非图片和损坏数据必须抛出不可静默忽略的 `ImageProviderError`。

- [ ] **Step 5: 实现 Base64 解码与 MinIO 归档**

```python
object_key = generate_object_key(tenant_id, f"generated.{extension}", prefix="generated-images")
storage.upload_bytes(object_key, image_bytes, content_type)
return GeneratedImage(url=storage.get_url(object_key), provider="openai_compatible", model=model)
```

- [ ] **Step 6: 运行适配器测试确认通过**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_openai_compatible_image_provider.py -q`

Expected: PASS。

### Task 3: 万相适配器与主备路由

**Files:**
- Create: `backend/app/services/wanxiang_image_provider.py`
- Create: `backend/app/services/image_generation_service.py`
- Modify: `backend/app/services/wanxiang_service.py`
- Test: `backend/tests/test_image_generation_fallback.py`

- [ ] **Step 1: 写主成功不降级测试**

```python
@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback():
    result = await service.generate(request)
    assert result.provider == "openai_compatible"
    assert fallback.calls == 0
```

- [ ] **Step 2: 写临时错误降级测试**

```python
@pytest.mark.asyncio
async def test_temporary_primary_failure_uses_wanxiang():
    primary.error = ImageProviderError("timeout", ImageErrorCategory.TEMPORARY)
    result = await service.generate(request)
    assert result.provider == "wanxiang"
    assert result.fallback_used is True
```

- [ ] **Step 3: 写鉴权错误不降级和双失败测试**

鉴权错误必须原样抛出且万相调用数为零；双失败异常必须同时包含主、备提供商诊断，但不包含密钥或完整签名 URL。

- [ ] **Step 4: 运行测试确认路由模块缺失而失败**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_fallback.py -q`

Expected: FAIL。

- [ ] **Step 5: 实现万相适配器和主备路由**

万相适配器把统一尺寸转换回万相格式并复用 `WanxiangImageService.generate_image()`。主备路由只在 `error.can_fallback` 为真时调用万相，并将 `fallback_used=True` 写入结果和日志。

- [ ] **Step 6: 运行主备测试确认通过**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_fallback.py -q`

Expected: PASS。

### Task 4: ERP 参考图字节直传

**Files:**
- Modify: `backend/app/services/scheduled_erp_image_service.py`
- Modify: `backend/app/schemas/article.py`
- Modify: `backend/app/services/article_agent_service.py`
- Test: `backend/tests/test_scheduled_erp_image_policy.py`
- Test: `backend/tests/test_article_image_provider_routing.py`

- [ ] **Step 1: 写 ERP 准备结果包含参考图字节的失败测试**

`PreparedErpImage` 必须包含规范化后的 `reference_image_bytes` 和 `reference_content_type`，同时保留 `reference_url` 供万相降级。

- [ ] **Step 2: 运行测试确认失败**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_scheduled_erp_image_policy.py -q`

Expected: FAIL，缺少字节字段。

- [ ] **Step 3: 扩展状态并通过统一服务生成**

`ArticleState` 增加参考图字节字段；`agent5_generate_images` 不再实例化 `WanxiangImageService`，改为构造统一请求：

```python
generated = await image_generation_service.generate(
    ImageGenerationRequest(
        prompt=prompt,
        size="1024*1365",
        tenant_id=state.tenant_id,
        reference_image_bytes=state.reference_image_bytes,
        reference_content_type=state.reference_content_type,
        reference_image_url=state.reference_image_url,
    )
)
```

- [ ] **Step 4: 运行 ERP 和文章路由测试确认通过**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_scheduled_erp_image_policy.py backend/tests/test_article_image_provider_routing.py -q`

Expected: PASS。

### Task 5: 迁移其余 AI 生图入口

**Files:**
- Modify: `backend/app/services/image_service_v2.py`
- Modify: `backend/app/agent/nodes/image_generation_node.py`
- Modify: `backend/app/services/imitation_service.py`
- Modify: `backend/app/services/job_queue_service.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/app/tasks/content_tasks.py`
- Modify: `backend/app/api/v1/articles.py`
- Test: `backend/tests/test_image_generation_entrypoints.py`

- [ ] **Step 1: 写入口依赖收敛失败测试**

AST 测试扫描业务入口，禁止出现 `from app.services.wanxiang_service import WanxiangImageService`，仅允许 `wanxiang_image_provider.py` 依赖旧万相服务。

- [ ] **Step 2: 运行测试确认当前直接依赖导致失败**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_entrypoints.py -q`

Expected: FAIL 并列出直接依赖文件。

- [ ] **Step 3: 把所有入口改为统一服务**

保留前端和历史任务中的 `DASHSCOPE` 值作为“AI 生成”兼容标识。所有生成结果使用统一结果的 `url`；日志中的 `method` 写实际提供商和是否降级。

- [ ] **Step 4: 移除旧节点中的二次万相回退**

主备路由已经集中处理降级，旧节点不得再次调用万相，否则会重复计费。随机图库和本地占位图仍不得伪装成生成成功。

- [ ] **Step 5: 运行入口与原有诊断测试**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_image_generation_entrypoints.py backend/tests/test_image_generation_node.py backend/tests/test_image_generation_diagnostics.py backend/tests/test_wanxiang_diagnostics.py -q`

Expected: PASS。

### Task 6: 本地配置、真实集成与定时任务验证

**Files:**
- Modify: `backend/.env`（Git 忽略，仅本地）
- Test: `backend/tests/test_openai_compatible_image_provider.py`
- Test: `backend/tests/test_image_generation_fallback.py`

- [ ] **Step 1: 写入本地中转站配置**

将用户提供的密钥写入 `backend/.env`，不输出、不提交。设置主提供商为 `openai_compatible`、降级提供商为 `wanxiang`。

- [ ] **Step 2: 运行隔离回归测试**

Run: `backend\venv\Scripts\python.exe -m pytest backend/tests/test_openai_compatible_image_provider.py backend/tests/test_image_generation_fallback.py backend/tests/test_scheduled_erp_image_policy.py backend/tests/test_article_image_provider_routing.py backend/tests/test_image_generation_entrypoints.py backend/tests/test_scheduled_cos_cleanup.py backend/tests/test_scheduled_publish_result.py -q`

Expected: 全部 PASS。

- [ ] **Step 3: 运行编译和差异检查**

Run: `backend\venv\Scripts\python.exe -m py_compile <本次修改的 Python 文件>`

Run: `git diff --check`

Expected: 退出码均为 0。

- [ ] **Step 4: 重启正式 Worker 并核对配置**

Run: `docker restart wechat-celery-worker`

容器内只打印主提供商、模型名和密钥是否存在，不打印密钥值；确认 Worker ready。

- [ ] **Step 5: 真实文本生图和 ERP 图生图验证**

各执行一次真实请求，确认结果归档为 MinIO URL而非数据 URI；再模拟主提供商超时，确认万相返回有效 URL。

- [ ] **Step 6: 跑一条临近时间定时任务到主号草稿**

确认运行记录 `completed`、文章 `draft_saved`、正文图片数量为五至七、封面为正文首张生成图、COS 临时对象计数为零。验证后恢复正式发布时间。
