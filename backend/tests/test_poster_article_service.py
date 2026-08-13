"""纯海报文章编排测试。"""

import json
from dataclasses import replace

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

    # 公众号标题必须让读者一眼知道文章对应什么产品；图片内文案仍然不泄露型号。
    assert "餐桌" in plan.article_title
    assert len(plan.posters) == 4
    assert "维多利亚餐桌" in captured["request"].user_message
    assert "不得出现产品型号" in captured["request"].user_message
    assert all("维多利亚餐桌" not in poster.copy for poster in plan.posters)


@pytest.mark.asyncio
async def test_poster_plan_applies_brand_title_template_without_shortening_to_legacy_limit():
    """品牌模板应让公众号标题保留产品关联和完整短句，而不是截为十二字标签。"""
    from app.services.poster_article_service import generate_poster_plan
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format(
        """【文章形式】纯海报拼接形式，无独立文字段落。
【标题要求】海报主标题不超过12字；公众号草稿标题自然包含产品名称。
【图片要求】竖版长海报比例，产品主体清晰。"""
    )
    captured = {}

    async def fake_complete(request):
        captured["request"] = request
        return json.dumps({
            "article_title": "奥诺拉沙发：把安静留给客厅，也留给每一次回家",
            "posters": [{"copy": "光线沿着扶手慢慢落下，让客厅留下更从容的停留。", "scene": "客厅"}],
        }, ensure_ascii=False)

    plan = await generate_poster_plan(
        profile=profile,
        product_name="奥诺拉沙发",
        style="xiehuai_oriental_living",
        complete_text=fake_complete,
    )

    assert "奥诺拉沙发" in plan.article_title
    assert len(plan.article_title) > 12
    assert "标题可以稍长" in captured["request"].user_message
    assert "标题最大 26 字" in captured["request"].user_message


@pytest.mark.asyncio
async def test_programmatic_three_poster_plan_uses_body_copy_for_every_panel_and_filters_model_id():
    """通用三图模板的每一张都应是正文型文案，并过滤产品型号式标题。"""
    from app.services.poster_article_service import generate_poster_plan
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format(
        """【文章形式】纯海报拼接形式，无独立文字段落。
【文案要求】每张长图内嵌文案控制在60字左右，主标题不超过18字。
【图片要求】竖版长海报比例，背景柔和朦胧，产品主体清晰。"""
    )
    profile = replace(profile, poster_count=2)

    async def fake_complete(_request):
        return json.dumps({
            "article_title": "fssf20198150",
            "title_poster_copy": "静候一室光",
            "posters": [
                {"copy": "让柔和的光线沿着沙发边缘落下，空间因此多了一点从容。", "scene": "客厅日常"},
                {"copy": "坐下、停留与交流，都从舒适的尺度和细节开始发生。", "scene": "生活片段"},
            ],
        }, ensure_ascii=False)

    plan = await generate_poster_plan(
        profile=profile,
        product_name="沙发·fssf20198150",
        body_copy_only=True,
        complete_text=fake_complete,
    )

    assert "fssf20198150" not in plan.article_title
    assert "沙发" in plan.article_title
    assert len(plan.posters) == 3
    assert all(len(poster.copy) >= 16 for poster in plan.posters)
    assert all(any(mark in poster.copy for mark in "，。；！？") for poster in plan.posters)
    assert all(poster.scene != "标题海报" for poster in plan.posters)


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


@pytest.mark.asyncio
async def test_poster_images_share_one_background_visual_anchor():
    """同一篇海报的独立图片必须共享知识库背景视觉规则。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【品牌调性】暖色、克制、现代家居，保持自然材质与空间比例。
【背景要求】所有图片使用同一类高级自然家居空间、柔和暖光和低饱和木色背景。
【图片要求】产品为唯一真实主体，文字固定预留在画面上半部分居中区域。
""")
    plan = PosterPlan(
        article_title="一桌之间",
        posters=(
            PosterText(copy="一桌之间", scene="标题空间"),
            PosterText(copy="材质在光线里安静展开。", scene="材质细节"),
        ),
    )
    received = []

    async def fake_generate(request):
        received.append(request)
        return f"https://cdn.example.com/{len(received)}.png"

    await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="实木餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        generate_image=fake_generate,
    )

    assert len(received) == 2
    assert all("本篇统一背景视觉锚点" in request.prompt for request in received)
    assert all("同一类高级自然家居空间" in request.prompt for request in received)
    assert "标题空间" in received[0].prompt
    assert "材质细节" in received[1].prompt


@pytest.mark.asyncio
async def test_poster_images_keep_real_product_visible_and_match_product_room():
    """海报图不能只剩朦胧氛围，产品和功能空间必须进入每张提示词。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format
    from app.services.scheduled_product_scene_service import resolve_product_scene_profile

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】竖版长海报比例，背景柔和朦胧，产品主体清晰，禁止内嵌二维码。
""")
    plan = PosterPlan(
        article_title="一席成景",
        posters=(PosterText(copy="一席成景", scene="柔和用餐场景"),),
    )
    received = []

    async def fake_generate(request):
        received.append(request)
        return "https://cdn.example.com/poster.png"

    scene_profile = resolve_product_scene_profile("现代餐桌")
    await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="现代餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        product_scene_profile=scene_profile,
        generate_image=fake_generate,
    )

    prompt = received[0].prompt
    assert "参考图中的 ERP 产品必须清晰可见" in prompt
    assert "餐厅" in prompt
    assert "沙发" in prompt
    assert "产品-场景一致性硬约束" in prompt


@pytest.mark.asyncio
async def test_poster_images_retry_low_information_result_once():
    """海报图片出现空白结果时，必须自动补充修复提示并只重试一次。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format
    from app.services.scheduled_image_quality_service import ImageQualityReport

    profile = analyze_publication_format(
        """【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】竖版长海报比例，产品主体清晰。"""
    )
    plan = PosterPlan(
        article_title="一席成景",
        posters=(PosterText(copy="一席成景", scene="标题空间"),),
    )
    prompts = []
    quality_checks = 0

    async def fake_generate(request):
        prompts.append(request.prompt)
        return "https://cdn.example.com/poster.png"

    async def fake_quality(_url):
        nonlocal quality_checks
        quality_checks += 1
        return ImageQualityReport(
            is_usable=quality_checks == 2,
            reason="低信息量：图片接近纯色",
        )

    urls = await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="现代餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        generate_image=fake_generate,
        quality_checker=fake_quality,
    )

    assert urls == ["https://cdn.example.com/poster.png"]
    assert len(prompts) == 2
    assert "低信息量结果修复" in prompts[1]


@pytest.mark.asyncio
async def test_poster_text_overlay_mode_does_not_send_copy_to_image_model():
    """程序叠字模式不把长文案重复发给图片模型，减少 token 并避免模型错字。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format(
        """【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】竖版长海报比例，产品主体清晰。"""
    )
    plan = PosterPlan(
        article_title="一席成景",
        posters=(PosterText(copy="这是一段只应由程序绘制的中文海报文案", scene="标题空间"),),
    )
    requests = []

    async def fake_generate(request):
        requests.append(request)
        return "https://cdn.example.com/poster.png"

    await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="现代餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        generate_image=fake_generate,
        embed_copy_in_model=False,
    )

    assert "程序叠加" in requests[0].prompt
    assert "这是一段只应由程序绘制的中文海报文案" not in requests[0].prompt


@pytest.mark.asyncio
async def test_programmatic_poster_images_generate_three_product_views_for_one_story():
    """程序叠字海报必须为同一产品生成三种机位，不能再复制一张图切三段。"""
    from app.services.poster_article_service import PosterPlan, PosterText, generate_poster_images
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【图片要求】竖版长海报比例，产品主体清晰。""")
    plan = PosterPlan(
        article_title="东意西形",
        posters=(
            PosterText(copy="东意西形", scene="标题空间"),
            PosterText(copy="让空间回到从容。", scene="意境海报"),
            PosterText(copy="木与光，在日常里安静相遇。", scene="生活场景"),
        ),
    )
    requests = []

    async def fake_generate(request):
        requests.append(request)
        return f"https://cdn.example.com/view-{len(requests)}.png"

    urls = await generate_poster_images(
        profile=profile,
        plan=plan,
        product_name="现代餐桌",
        tenant_id=107,
        reference_image_bytes=b"erp-image",
        reference_content_type="image/jpeg",
        generate_image=fake_generate,
        embed_copy_in_model=False,
    )

    assert urls == [
        "https://cdn.example.com/view-1.png",
        "https://cdn.example.com/view-2.png",
        "https://cdn.example.com/view-3.png",
    ]
    assert len(requests) == 3
    assert "空间引入广角" in requests[0].prompt
    assert "完整产品主视觉" in requests[1].prompt
    assert "材质、结构、局部细节或侧后角度" in requests[2].prompt
    assert all("同一 ERP 产品、同一房间、同色温、同光向" in request.prompt for request in requests)


@pytest.mark.asyncio
async def test_poster_plan_falls_back_to_product_related_title_and_story_copy():
    """模型返回无关标题和短语时，发布前必须收口为产品相关的正文型海报文案。"""
    from app.services.poster_article_service import generate_poster_plan
    from app.services.publication_format_service import analyze_publication_format

    profile = analyze_publication_format("""【文章形式】纯海报拼接形式，无独立文字段落。
【文案要求】每张长图内嵌文案控制在50字左右，主标题不超过20字。
【图片要求】竖版长海报比例，产品主体清晰。""")

    async def fake_complete(_request):
        return json.dumps({
            "article_title": "在曲奇的潮流中，我们向",
            "title_poster_copy": "透空诗境",
            "posters": [
                {"copy": "光的回响", "scene": "餐厅"},
                {"copy": "一席之间，木纹和餐具在柔和光线里相互映照，让每一次围坐都更从容。", "scene": "餐桌细节"},
            ],
        }, ensure_ascii=False)

    plan = await generate_poster_plan(
        profile=profile,
        product_name="简约岩板餐桌",
        complete_text=fake_complete,
    )

    assert "餐桌" in plan.article_title
    assert "曲奇" not in plan.article_title
    # 第三张（最后一个内容海报）不允许只剩四到六个字的标题式短语。
    assert len(plan.posters[1].copy) >= 16
    assert any(mark in plan.posters[1].copy for mark in "，。；")
