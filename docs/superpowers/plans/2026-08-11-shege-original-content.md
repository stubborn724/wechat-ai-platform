# 她格原创公众号任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为主号配置基于她格知识库的独立原创图文生产链路，并建立每日公域、私域定时发布任务。

**Architecture:** 保持现有定时任务执行器不变，通过独立知识库和两个任务记录隔离她格业务。补齐通用咨询卡片在没有二维码时的电话展示能力；初始化脚本只对“她格”命名空间执行幂等创建和更新，避免影响现有绣蔓及海报任务。

**Tech Stack:** FastAPI、SQLAlchemy、MySQL、PostgreSQL 知识库、pytest、Docker Compose。

---

### Task 1: 支持无二维码咨询卡片

**Files:**
- Modify: `backend/app/services/footer_template_service.py`
- Modify: `backend/tests/test_footer_template_service.py`

- [x] **Step 1: 编写电话-only 咨询卡片失败测试**

```python
def test_render_consultation_card_keeps_phone_when_qrcodes_are_empty():
    html = render_footer_template({
        "type": "consultation_card_v1",
        "brand": "她格",
        "headline": "企业 AI 转型咨询",
        "phone": "18613093631",
        "qrcodes": [],
    })

    assert "18613093631" in html
    assert "企业 AI 转型咨询" in html
    assert "扫码" not in html
```

- [x] **Step 2: 运行测试并确认当前行为失败**

Run: `docker compose exec backend pytest tests/test_footer_template_service.py::test_render_consultation_card_keeps_phone_when_qrcodes_are_empty -q`

Expected: FAIL，因为现有实现没有二维码时直接返回空字符串。

- [x] **Step 3: 实现电话-only 卡片渲染**

```python
if not safe_codes and not safe_phone:
    return ""

phone_html = _render_phone_block(safe_phone) if safe_phone else ""
qrcode_html = _render_qrcode_grid(safe_codes) if safe_codes else ""
hint_html = "<p>扫码或长按识别二维码</p>" if safe_codes else ""
```

保留有二维码时的既有网格、文案和样式；无二维码时只渲染品牌、标题和电话。

- [x] **Step 4: 运行咨询卡片测试**

Run: `docker compose exec backend pytest tests/test_footer_template_service.py -q`

Expected: PASS。

### Task 2: 创建她格知识库与定时任务初始化器

**Files:**
- Create: `backend/scripts/initialize_shege_original_tasks.py`
- Test: `backend/tests/test_initialize_shege_original_tasks.py`

- [x] **Step 1: 编写初始化配置失败测试**

```python
def test_build_shege_task_specs_uses_standard_article_without_erp_or_poster():
    specs = build_shege_task_specs(knowledge_base_ids=[11, 12], account_id=103)

    assert specs["她格原创-公域"]["publish_times"] == ["13:00"]
    assert specs["她格原创-私域"]["publish_times"] == ["08:00", "20:00"]
    for specification in specs.values():
        assert specification["content_type"] == "article"
        assert specification["layout_mode"] == "standard"
        assert specification["erp_image_config"] is None
        assert specification["footer_template"]["phone"] == "18613093631"
        assert specification["footer_template"]["qrcodes"] == []
```

- [x] **Step 2: 运行测试并确认模块尚不存在**

Run: `docker compose exec backend pytest tests/test_initialize_shege_original_tasks.py -q`

Expected: FAIL，因为初始化模块尚不存在。

- [x] **Step 3: 编写幂等初始化脚本**

```python
def build_shege_task_specs(knowledge_base_ids: list[int], account_id: int) -> dict[str, dict]:
    """构造她格任务专属配置，避免复用 ERP、海报或来源模板字段。"""

def ensure_shege_knowledge_bases(session) -> list[int]:
    """以两个 docx 的解析文本创建或更新她格知识库文档。"""

def ensure_shege_scheduled_tasks(session, knowledge_base_ids: list[int]) -> None:
    """只按任务名称创建或更新她格任务，其他任务不读取也不写入。"""
```

脚本应：从两个指定 DOCX 提取文本，按稳定名称建立她格独立知识库和文档；查找 `主号` 或固定 ID `103`；创建/更新 `她格原创-公域`、`她格原创-私域`；两任务均设置 `article`、`standard`、两个知识库 ID、电话-only `consultation_card_v1`，并清空 ERP、海报、格式模板等专用配置。公域按既有公域任务语义设置；私域按既有私域任务语义设置。

- [x] **Step 4: 运行脚本配置单元测试**

Run: `docker compose exec backend pytest tests/test_initialize_shege_original_tasks.py -q`

Expected: PASS。

### Task 3: 应用和验证实际配置

**Files:**
- Execute: `backend/scripts/initialize_shege_original_tasks.py`

- [x] **Step 1: 检查既有公域、私域任务的投递字段**

Run: `docker compose exec mysql mysql -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "SELECT name,publish_domain,publish_mode,publish_times,account_ids FROM scheduled_tasks WHERE publish_domain IN ('public','private');"`

Expected: 获得生产中公域和私域的真实字段组合，作为她格任务配置依据。

- [x] **Step 2: 执行幂等初始化脚本**

Run: `docker compose exec backend python scripts/initialize_shege_original_tasks.py`

Expected: 输出两个知识库和两个任务的 ID，重复执行不创建重复记录。

- [x] **Step 3: 查询她格任务隔离配置**

Run: `docker compose exec mysql mysql -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "SELECT name,publish_domain,publish_times,account_ids,content_type,layout_mode,knowledge_base_ids,erp_image_config,footer_template FROM scheduled_tasks WHERE name LIKE '她格原创-%';"`

Expected: 仅存在两个她格任务，绑定 `主号`，无 ERP/海报配置，时间分别为 `13:00` 和 `08:00,20:00`。

- [ ] **Step 4: 使用草稿箱进行一次独立验证**

在不改变日常任务发布时间和域配置的前提下，创建一次性验证任务或调用现有手动触发链路，明确使用草稿箱，以确认知识库正文、标准图文和电话-only 底部组件均能生成。

- [x] **Step 5: 运行回归测试并检查任务状态**

Run: `docker compose exec backend pytest tests/test_footer_template_service.py tests/test_initialize_shege_original_tasks.py -q`

Expected: PASS；任务配置保持幂等。
