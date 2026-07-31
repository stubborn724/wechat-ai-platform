# Quality-Preserving Prompt Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低 ERP 图生图任务中重复背景知识库输入的 Token，同时不改变投喂文章的图文槽位结构或图片质量约束。

**Architecture:** HTML 槽位内容 Agent 一次接收完整背景规范并为每张原位图片生成最终视觉提示词。图生图执行层只消费该槽位提示词与产品保真规则；缺少任一提示词时中止任务。

**Tech Stack:** Python、pytest、BeautifulSoup、OpenAI 兼容图像服务。

---

### Task 1: 验证 ERP 槽位 Agent 接收完整背景规范

**Files:**
- Modify: `backend/tests/test_html_imitation_service.py`
- Modify: `backend/app/services/article_agent_service.py`

- [ ] **Step 1: 编写失败测试**

```python
assert "图片背景知识库" in captured_prompt
assert "墨绿、古铜金" in captured_prompt
```

- [ ] **Step 2: 运行测试，确认当前 Prompt 不含背景规范**

Run: `pytest tests/test_html_imitation_service.py -k erp_html -v`

- [ ] **Step 3: 在 HTML 槽位 Prompt 中加入 `state.image_prompt_context`**

- [ ] **Step 4: 重跑测试**

Run: `pytest tests/test_html_imitation_service.py -k erp_html -v`

### Task 2: 图生图只使用一次生成的槽位提示词

**Files:**
- Modify: `backend/tests/test_article_auto_mode.py`
- Modify: `backend/app/services/article_agent_service.py`

- [ ] **Step 1: 编写失败测试**

```python
assert "品牌视觉约束：" not in request.prompt
assert "暖色现代客厅" in request.prompt
```

- [ ] **Step 2: 图生图入口移除重复知识库拼接，并拒绝空槽位提示词**

- [ ] **Step 3: 重跑目标测试**

Run: `pytest tests/test_article_auto_mode.py -k erp -v`

### Task 3: 回归验证原位与少图策略

**Files:**
- Modify: `backend/tests/test_html_imitation_service.py`

- [ ] **Step 1: 增加少于原图槽位时保留空容器的回归测试**
- [ ] **Step 2: 运行 HTML 和定时上下文测试**

Run: `pytest tests/test_html_imitation_service.py tests/test_scheduled_article_context_service.py -v`
