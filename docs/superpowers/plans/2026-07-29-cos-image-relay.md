# 腾讯 COS 图片中转实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ERP 定时图文任务通过腾讯 COS 私有签名 URL 调用万相图生图，并且只有微信公众号草稿真实保存成功时才将任务标记为完成。

**Architecture:** 保留现有 MinIO 作为长期素材库，新增独立 `CosImageRelayService` 作为短时公网中转。ERP 图片先归档 MinIO，再读取归档字节上传 COS；定时任务在 `finally` 中删除 COS 对象。万相和微信发布失败均向上抛出，杜绝假成功。

**Tech Stack:** FastAPI、Pydantic Settings、SQLAlchemy、腾讯云 COS Python SDK、Pytest、Celery

---

### Task 1: COS 配置与中转服务

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/requirements.txt`
- Modify: `.env.example`
- Create: `backend/app/services/cos_image_relay_service.py`
- Create: `backend/tests/test_cos_image_relay_service.py`

- [ ] **Step 1: 编写配置缺失、上传签名和清理测试**

```python
def test_cos_relay_rejects_incomplete_config():
    with pytest.raises(CosImageRelayConfigurationError, match="COS_BUCKET"):
        CosImageRelayService(settings=FakeSettings(cos_bucket=""), client=FakeClient())


def test_cos_relay_uploads_and_returns_https_signed_url():
    relay = CosImageRelayService(settings=complete_settings(), client=FakeClient())
    result = relay.stage_bytes(b"image", "image/jpeg", tenant_id=107, run_id=6)
    assert result.object_key.startswith("temporary/107/6/")
    assert result.signed_url.startswith("https://")


def test_cos_relay_deletes_exact_object_key():
    relay.delete_object("temporary/107/6/image.jpg")
    assert client.deleted_keys == ["temporary/107/6/image.jpg"]
```

- [ ] **Step 2: 运行测试确认因服务不存在而失败**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_cos_image_relay_service.py -q`
Expected: FAIL，提示 `app.services.cos_image_relay_service` 不存在。

- [ ] **Step 3: 增加配置和 COS 服务**

```python
@dataclass(frozen=True)
class CosRelayObject:
    object_key: str
    signed_url: str


class CosImageRelayService:
    """将单次任务图片短暂中转到私有 COS，并生成外部服务可读取的签名 URL。"""

    def stage_bytes(self, data: bytes, content_type: str, tenant_id: int, run_id: int) -> CosRelayObject:
        object_key = f"temporary/{tenant_id}/{run_id}/{uuid.uuid4().hex}.{extension}"
        self.client.put_object(Bucket=self.bucket, Key=object_key, Body=data, ContentType=content_type)
        signed_url = self.client.get_presigned_url(
            Method="GET", Bucket=self.bucket, Key=object_key,
            Expired=self.signed_url_expire_seconds,
        )
        return CosRelayObject(object_key=object_key, signed_url=signed_url)
```

配置字段为 `COS_ENABLED`、`COS_SECRET_ID`、`COS_SECRET_KEY`、`COS_REGION`、`COS_BUCKET` 和 `COS_SIGNED_URL_EXPIRE_SECONDS`。SDK 依赖使用 `cos-python-sdk-v5`。

- [ ] **Step 4: 运行 COS 服务测试**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_cos_image_relay_service.py -q`
Expected: PASS。

### Task 2: ERP 图片归档后中转到 COS

**Files:**
- Modify: `backend/app/services/scheduled_erp_image_service.py`
- Modify: `backend/tests/test_scheduled_erp_image_policy.py`

- [ ] **Step 1: 编写 ERP 准备结果使用 COS URL 的失败测试**

```python
@pytest.mark.asyncio
async def test_prepared_erp_image_contains_cos_reference_url(monkeypatch):
    result = await prepare_erp_images_for_scheduled_run(..., relay_service=fake_relay)
    assert result[0].reference_url == "https://cos.example.com/signed-image"
    assert result[0].relay_object_key == "temporary/107/6/image.jpg"
```

- [ ] **Step 2: 运行测试确认字段和依赖注入尚不存在**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_erp_image_policy.py -q`
Expected: FAIL，提示 `reference_url` 或 `relay_service` 不存在。

- [ ] **Step 3: 扩展准备结果并复用本地归档字节**

```python
@dataclass(frozen=True)
class PreparedErpImage:
    product: ErpProduct
    asset_id: int
    local_url: str
    reference_url: str
    relay_object_key: str
```

从 `Asset.storage_key` 读取 MinIO 字节后调用 `relay_service.stage_bytes`，并将 COS 签名 URL 作为万相参考图地址。本地 URL 只用于素材库展示。

- [ ] **Step 4: 运行 ERP 策略测试**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_erp_image_policy.py backend/tests/test_cos_image_relay_service.py -q`
Expected: PASS。

### Task 3: 万相图生图失败必须终止任务并清理 COS

**Files:**
- Modify: `backend/app/services/article_agent_service.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_image_generation_node.py`
- Create: `backend/tests/test_scheduled_cos_cleanup.py`

- [ ] **Step 1: 编写生成数量不足和异常清理测试**

```python
@pytest.mark.asyncio
async def test_reference_generation_fails_when_any_required_image_is_missing(monkeypatch):
    monkeypatch.setattr(WanxiangImageService, "generate_image", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="图生图失败"):
        await agent5_generate_images(state_with_reference_image)


def test_scheduled_pipeline_deletes_cos_object_on_failure():
    with pytest.raises(RuntimeError):
        run_pipeline_that_fails()
    assert fake_relay.deleted_keys == ["temporary/107/6/image.jpg"]
```

- [ ] **Step 2: 运行测试确认当前流程静默返回空图片**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_image_generation_node.py backend/tests/test_scheduled_cos_cleanup.py -q`
Expected: FAIL，当前实现不会抛错或不会清理。

- [ ] **Step 3: 增加强制失败与 finally 清理**

生成结果数量少于图片需求数量时抛出 `RuntimeError`。`_scheduled_article` 收集本次 COS 对象键，并在每个文章槽位的 `finally` 中逐个删除；清理异常只记告警，不覆盖主异常。

- [ ] **Step 4: 运行图生图和清理测试**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_image_generation_node.py backend/tests/test_scheduled_cos_cleanup.py backend/tests/test_wanxiang_diagnostics.py -q`
Expected: PASS。

### Task 4: 微信草稿真实成功后才完成定时任务

**Files:**
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Create: `backend/tests/test_scheduled_publish_result.py`

- [ ] **Step 1: 编写公众号失败向上抛出测试**

```python
def test_publish_to_wechat_raises_when_draft_save_fails(monkeypatch):
    monkeypatch.setattr("app.services.wechat_publisher.publish_article", failing_publish)
    with pytest.raises(RuntimeError, match="公众号 #103"):
        _publish_to_wechat(db, article, [103], "draft", task)
```

- [ ] **Step 2: 运行测试确认当前实现吞掉异常**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_publish_result.py -q`
Expected: FAIL，当前 `_publish_to_wechat` 只打印告警。

- [ ] **Step 3: 调整文章和运行状态更新顺序**

文章生成后先保存为 `generated`，调用 `_publish_to_wechat` 成功后再写入 `draft_saved` 或 `published`。任一配置的公众号失败时抛出包含账号 ID 的异常，由 `execute_scheduled_article` 将 `ScheduledTaskRun` 更新为 `failed`。

- [ ] **Step 4: 运行发布结果与相关回归测试**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_scheduled_publish_result.py backend/tests/test_image_generation_diagnostics.py backend/tests/test_wanxiang_diagnostics.py -q`
Expected: PASS。

### Task 5: 本地配置与端到端验收

**Files:**
- Modify: `backend/.env`（Git 忽略，仅本机）

- [ ] **Step 1: 写入用户提供的 COS 凭证和 Bucket 配置**

配置 `COS_ENABLED=true`、`COS_REGION=ap-guangzhou`、`COS_BUCKET=wqj123456-1349906872`、签名有效期 3600 秒，以及用户提供的 SecretId/SecretKey。日志和提交中不得出现密钥值。

- [ ] **Step 2: 安装依赖并验证 COS 上传、签名 URL 下载和删除**

Run: `backend/venv/Scripts/python.exe -m pip install cos-python-sdk-v5`
Expected: 安装成功；测试对象能通过 HTTPS 签名 URL读取，随后从 Bucket 删除。

- [ ] **Step 3: 启动正确 Redis 队列的 Celery Worker**

使用项目独立 Redis 队列启动 Worker，日志写入 `backend/logs/scheduled-cos-worker.log`，并确认显示 `ready`。

- [ ] **Step 4: 将任务 #4 临时设置为当前分钟并触发调度**

任务保持主号 `#103`、`draft` 模式。记录测试前发布时间，创建新的 `ScheduledTaskRun` 后持续检查日志和数据库状态。

- [ ] **Step 5: 验证草稿成功并恢复正式时间**

验收数据库运行状态为 `completed`、文章状态为 `draft_saved`，后端日志包含 COS 中转、万相成功和公众号草稿成功；随后将任务 #4 恢复到 `17:00` 并停止临时测试进程。

- [ ] **Step 6: 运行最终回归测试和静态检查**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_cos_image_relay_service.py backend/tests/test_scheduled_erp_image_policy.py backend/tests/test_image_generation_node.py backend/tests/test_scheduled_cos_cleanup.py backend/tests/test_scheduled_publish_result.py backend/tests/test_wanxiang_diagnostics.py backend/tests/test_image_generation_diagnostics.py -q`

Run: `backend/venv/Scripts/python.exe -m py_compile backend/app/services/cos_image_relay_service.py backend/app/services/scheduled_erp_image_service.py backend/app/services/article_agent_service.py backend/app/tasks/scheduled_task_executor.py`

Expected: 全部通过，且 `git diff --check` 无错误。
