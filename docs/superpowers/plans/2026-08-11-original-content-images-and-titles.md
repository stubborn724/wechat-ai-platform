# Original Content Images And Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HerGe original articles insert knowledge-base-aligned images into their body, and improve titles for HerGe plus the three independent poster brands without changing Xiuman or imitation tasks.

**Architecture:** A small original-content policy service identifies only the four reusable writing-template identifiers. A deterministic HTML/Markdown image insertion service maps generated images to article headings, while the existing generation agents retain responsibility for image prompts and model calls. The scheduled executor calls the insertion service only for HerGe standard articles. Poster title behavior remains in the poster service and uses refined template prompts.

**Tech Stack:** Python 3.11, FastAPI/Celery, SQLAlchemy, pytest.

---

### Task 1: Original Article Image Insertion Service

**Files:**
- Create: `backend/app/services/original_article_image_service.py`
- Create: `backend/tests/test_original_article_image_service.py`
- Modify: `backend/app/tasks/scheduled_task_executor.py`

- [ ] **Step 1: Write failing tests**

```python
def test_insert_images_after_markdown_headings_keeps_article_order():
    content = "# 标题\n\n## 第一节\n内容\n\n## 第二节\n内容"
    rendered = insert_original_article_images(content, ["https://cdn/1.png", "https://cdn/2.png"])
    assert rendered.index("https://cdn/1.png") > rendered.index("## 第一节")
    assert rendered.index("https://cdn/2.png") > rendered.index("## 第二节")

def test_non_original_style_does_not_enable_body_image_insertion():
    assert should_insert_original_article_images("default") is False
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m pytest backend/tests/test_original_article_image_service.py -q`

Expected: failure because the service module does not exist.

- [ ] **Step 3: Implement a deterministic insertion service**

```python
def should_insert_original_article_images(style: str | None) -> bool:
    return style == SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID

def insert_original_article_images(content: str, image_urls: Sequence[str]) -> str:
    # Split at H2 headings; inject each non-empty URL after the matching section heading.
    ...
```

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest backend/tests/test_original_article_image_service.py -q`

Expected: all pass.

- [ ] **Step 5: Connect only the HerGe standard-article branch**

After Agent 4/5 generation and normal merging, call the service only when the task style is `shege_enterprise_ai_service`. Preserve existing output when no generated image URL exists.

### Task 2: Image Prompt Context And Persistence

**Files:**
- Modify: `backend/app/tasks/scheduled_task_executor.py`
- Modify: `backend/tests/test_original_article_image_service.py`

- [ ] **Step 1: Write failing executor-contract tests**

```python
def test_shege_image_prompt_receives_knowledge_context_and_section_text():
    prompt = build_original_article_image_prompt(...)
    assert "知识库图片规则" in prompt
    assert "章节内容" in prompt
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m pytest backend/tests/test_original_article_image_service.py -q`

Expected: failure because no composed prompt helper exists.

- [ ] **Step 3: Implement prompt composition and insertion before persistence**

Extend the image requirement prompt only for HerGe with the existing `image_prompt_context`, selected title and section text. Merge generated images, insert them into `s.content`, then assign `s.full_content` from the rendered result so the published draft receives the same HTML.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest backend/tests/test_original_article_image_service.py -q`

Expected: all pass.

### Task 3: Four Brand Title Policies

**Files:**
- Modify: `backend/app/services/writing_style_template_service.py`
- Modify: `backend/tests/test_writing_style_template_service.py`
- Modify: `backend/tests/test_poster_article_service.py`

- [ ] **Step 1: Write failing assertions for long-form title policy**

```python
def test_four_original_templates_require_viewpoint_led_long_titles():
    for identifier in ORIGINAL_TEMPLATE_IDS:
        prompt = get_writing_style_template_prompt(identifier)
        assert "完整长句" in prompt
        assert "型号" in prompt
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest backend/tests/test_writing_style_template_service.py backend/tests/test_poster_article_service.py -q`

Expected: failure because the unified long-title requirements are absent.

- [ ] **Step 3: Refine reusable template prompts**

Use a common structural requirement: a semantic lead separated by `|` or Chinese punctuation and a product/business-related viewpoint sentence. HerGe uses business-problem language; the three poster brands use product and spatial-aesthetic language. Preserve each brand's existing voice and no-model-ID requirement.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest backend/tests/test_writing_style_template_service.py backend/tests/test_poster_article_service.py -q`

Expected: all pass.

### Task 4: Regression Verification

**Files:**
- Test: `backend/tests/test_original_article_image_service.py`
- Test: `backend/tests/test_writing_style_template_service.py`
- Test: `backend/tests/test_poster_article_service.py`

- [ ] **Step 1: Run targeted backend suite in the scheduled Worker container**

Run: `docker compose exec -T celery-scheduled-worker python -m pytest tests/test_original_article_image_service.py tests/test_writing_style_template_service.py tests/test_poster_article_service.py -q`

Expected: all pass.

- [ ] **Step 2: Verify task isolation in MySQL**

Check that only style identifiers `shege_enterprise_ai_service`, `zhongxiwujie_east_west_living`, `xiehuai_oriental_living`, and `jianzhi_artful_living` use the new policies; confirm Xiuman task records retain their existing style and layout configuration.
