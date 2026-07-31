"""纯海报文章编排测试。"""

import json

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """海报编排仅依赖注入的模型回调，不访问本地业务数据库。"""

    yield


@pytest.mark.asyncio
async def test_poster_plan_uses_product_as_semantic_context_but_not_image_copy():
    """产品用于画面主体约束，图片内文案不能被强制写入产品型号。"""
    from app.services.poster_article_service import generate_poster_plan
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【文案要求】每张长图内嵌文案控制在50字左右，主标题不超过12字，不显示产品型号。
【图片要求】竖版长海报比例，禁止内嵌二维码。
【末尾联系方式】固定显示联系方式文案“中西无界TEL: 18138381749”，并附上企业微信二维码图片：https://cdn.example.com/qr.png""")
    captured = {}

    async def fake_complete(request):
        captured["request"] = request
        return json.dumps({
            "article_title": "东意西形",
            "title_poster_copy": "东意西形",
            "posters": [
                {"copy": "以东方意蕴，回应当代空间的克制表达。", "scene": "客厅空间"},
                {"copy": "木与铜的细节，在静处显出分寸。", "scene": "材质细节"},
                {"copy": "让传统的气韵，与都会日常自然相遇。", "scene": "生活场景"},
            ],
        }, ensure_ascii=False)

    plan = await generate_poster_plan(
        profile=profile,
        product_name="维多利亚餐桌",
        complete_text=fake_complete,
    )

    assert plan.article_title == "东意西形"
    assert len(plan.posters) == 4
    assert "维多利亚餐桌" in captured["request"].user_message
    assert "不得出现产品型号" in captured["request"].user_message
    assert all("维多利亚餐桌" not in poster.copy for poster in plan.posters)


@pytest.mark.asyncio
async def test_poster_images_forward_reference_bytes_and_exact_embedded_copy():
    """每张海报都必须使用同一 ERP 原图，并接收完整的图片内文案。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】竖版长海报比例，禁止内嵌二维码。
【末尾联系方式】固定显示联系方式文案“中西无界TEL: 18138381749”，并附上企业微信二维码图片：https://cdn.example.com/qr.png""")
    plan = PosterPlan(
        article_title="东意西形",
        posters=(
            PosterText(copy="东意西形", scene="标题海报"),
            PosterText(copy="让空间回到从容。", scene="意境海报"),
        ),
    )
    received = []

    async def fake_generate(request):
        received.append(request)
        return f"https://cdn.example.com/{len(received)}.png"

    urls = await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="维多利亚餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        generate_image=fake_generate,
    )

    assert urls == ["https://cdn.example.com/1.png", "https://cdn.example.com/2.png"]
    assert all(request.reference_image_bytes == b"erp-image" for request in received)
    assert "东意西形" in received[0].prompt
    assert "让空间回到从容" in received[1].prompt
    assert all("禁止内嵌二维码" in request.prompt for request in received)
