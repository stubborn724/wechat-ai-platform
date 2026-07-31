# Wanxiang Diagnostic Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在万相图片生成失败时输出可诊断的后端日志，并阻止仿写流程返回随机图库图。

**Architecture:** 万相服务记录请求提交、HTTP 失败、轮询完成、任务失败和超时等结构化上下文；图片策略在没有生成 URL 时返回 `None`，由上游处理失败，不生成 Picsum URL。

**Tech Stack:** Python 3.11、pytest、httpx、logging。

---

### Task 1: 写出失败测试

**Files:**
- Create: `backend/tests/test_wanxiang_diagnostics.py`
- Modify: `backend/tests/test_image_generation_node.py`

- [ ] **Step 1: 测试万相 HTTP 失败日志**

```python
async def test_generate_image_logs_http_status_and_response_without_secret(monkeypatch, caplog):
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpClientReturning400)
    service = WanxiangImageService(api_key="secret-value")
    assert await service.generate_image("家具提示词") is None
    assert "status=400" in caplog.text
    assert "invalid model" in caplog.text
    assert "secret-value" not in caplog.text
```

- [ ] **Step 2: 测试图片策略不返回 Picsum URL**

```python
async def test_dashscope_service_returns_none_when_wanxiang_fails(monkeypatch, caplog):
    monkeypatch.setattr(WanxiangImageService, "generate_image", failing_generation)
    assert await DashScopeImageGenService().search_image("家具", prompt="完整提示词") is None
    assert "随机图库回退已阻止" in caplog.text
```

- [ ] **Step 3: 运行测试确认失败**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_wanxiang_diagnostics.py -q`

Expected: HTTP 日志缺少诊断字段，图片策略返回 Picsum URL。

### Task 2: 实现诊断日志和随机图拦截

**Files:**
- Modify: `backend/app/services/wanxiang_service.py`
- Modify: `backend/app/services/image_service_v2.py`
- Test: `backend/tests/test_wanxiang_diagnostics.py`

- [ ] **Step 1: 为万相服务增加安全日志辅助函数**

```python
def _summarize(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
```

- [ ] **Step 2: 在请求、HTTP 失败、任务失败和超时边界记录上下文**

```python
logger.info("Wanxiang submit model=%s size=%s prompt_len=%d prompt=%r", model, size, len(prompt), _summarize(prompt, 240))
logger.warning("Wanxiang submit failed status=%s response=%r", resp.status_code, _summarize(response_detail, 800))
logger.warning("Wanxiang task failed task_id=%s status=%s message=%r", task_id, task_status, _summarize(err_msg, 800))
```

- [ ] **Step 3: 禁止策略返回随机图库 URL**

```python
url = await self.wanxiang.generate_image(prompt, no_text=True)
if not url:
    logger.error("Wanxiang generation failed; random gallery fallback blocked")
return url
```

- [ ] **Step 4: 运行测试确认通过**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_wanxiang_diagnostics.py -q`

Expected: PASS。

### Task 3: 验证

**Files:**
- Modify: `backend/app/services/wanxiang_service.py`
- Modify: `backend/app/services/image_service_v2.py`
- Create: `backend/tests/test_wanxiang_diagnostics.py`

- [ ] **Step 1: 运行图片生成相关回归测试**

Run: `& 'venv/Scripts/python.exe' -m pytest tests/test_wanxiang_diagnostics.py tests/test_image_generation_node.py tests/test_reference_image_imitation_service.py -q`

Expected: PASS。

- [ ] **Step 2: 编译并检查空白**

Run: `& 'venv/Scripts/python.exe' -m py_compile app/services/wanxiang_service.py app/services/image_service_v2.py; git diff --check`

Expected: 命令退出码为 0。
