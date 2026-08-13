"""她格原创图文的正文配图服务测试。"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块只验证纯文本处理，不访问业务数据库。"""

    yield


def test_shege_style_is_the_only_original_article_style_that_inserts_body_images():
    """海报品牌与绣蔓不能被图文配图逻辑误命中。"""

    from app.services.original_article_image_service import (
        should_insert_original_article_images,
    )

    assert should_insert_original_article_images("shege_enterprise_ai_service") is True
    assert should_insert_original_article_images("xiehuai_oriental_living") is False
    assert should_insert_original_article_images("default") is False


def test_insert_images_places_related_images_after_matching_markdown_headings():
    """正文图片必须进入对应章节之后，不能只停留在封面或文末。"""

    from app.schemas.article import ImageResult
    from app.services.original_article_image_service import insert_original_article_images

    content = (
        "# 人工智能如何进入经营\n\n"
        "开篇内容。\n\n"
        "## 用客户分群改善获客\n"
        "第一段内容。\n\n"
        "## 用需求预测减少积压\n"
        "第二段内容。"
    )
    images = [
        ImageResult(
            position=1,
            url="https://cdn.example.com/customer.png",
            method="DASHSCOPE",
            section_title="用客户分群改善获客",
        ),
        ImageResult(
            position=2,
            url="https://cdn.example.com/inventory.png",
            method="DASHSCOPE",
            section_title="用需求预测减少积压",
        ),
    ]

    rendered = insert_original_article_images(content, images)

    customer_heading = rendered.index("## 用客户分群改善获客")
    inventory_heading = rendered.index("## 用需求预测减少积压")
    assert customer_heading < rendered.index("customer.png") < inventory_heading
    assert inventory_heading < rendered.index("inventory.png")


def test_insert_images_uses_section_order_when_model_returns_no_matching_heading():
    """图片分析偶尔返回概括节名时，仍按正文主章节顺序完整呈现。"""

    from app.schemas.article import ImageResult
    from app.services.original_article_image_service import insert_original_article_images

    content = "## 第一节\n内容一\n\n## 第二节\n内容二"
    images = [
        ImageResult(
            position=1,
            url="https://cdn.example.com/first.png",
            method="DASHSCOPE",
            section_title="模型概括的第一节",
        ),
        ImageResult(
            position=2,
            url="https://cdn.example.com/second.png",
            method="DASHSCOPE",
            section_title="模型概括的第二节",
        ),
    ]

    rendered = insert_original_article_images(content, images)

    assert rendered.index("## 第一节") < rendered.index("first.png") < rendered.index("## 第二节")
    assert rendered.index("## 第二节") < rendered.index("second.png")


def test_insert_images_recognises_plain_text_headings_emitted_by_the_article_model():
    """普通文本小标题不能退化为全部图片追加到文章末尾。"""

    from app.schemas.article import ImageResult
    from app.services.original_article_image_service import insert_original_article_images

    content = (
        "一眼看清问题：库存周转为何拖慢现金流  \n"
        "第一节正文。\n\n"
        "打基础：数据、系统与角色准备  \n"
        "第二节正文。"
    )
    images = [
        ImageResult(
            position=1,
            url="https://cdn.example.com/problem.png",
            method="DASHSCOPE",
            section_title="一眼看清问题：库存周转为何拖慢现金流",
        ),
        ImageResult(
            position=2,
            url="https://cdn.example.com/data.png",
            method="DASHSCOPE",
            section_title="打基础：数据、系统与角色准备",
        ),
    ]

    rendered = insert_original_article_images(content, images)

    first_heading = rendered.index("一眼看清问题")
    second_heading = rendered.index("打基础")
    assert first_heading < rendered.index("problem.png") < second_heading
    assert second_heading < rendered.index("data.png")


def test_shege_image_prompt_adds_knowledge_and_related_section_requirements():
    """她格图片必须依据知识库和章节内容生成，而非泛化办公配图。"""

    from app.services.original_article_image_service import (
        append_shege_image_requirement_context,
    )

    prompt = append_shege_image_requirement_context(
        "基础图片需求",
        style="shege_enterprise_ai_service",
        image_prompt_context="知识库图片规则：画面应为中小企业真实经营场景。",
    )

    assert "知识库图片规则" in prompt
    assert "真实经营场景" in prompt
    assert "对应章节" in prompt
