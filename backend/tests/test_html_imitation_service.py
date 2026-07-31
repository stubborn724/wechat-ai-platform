"""HTML 仿写结构服务的回归测试。

这些测试刻意不依赖数据库和大模型：格式保留与槽位回填必须由确定性的
程序完成，才能保证模型输出异常时不会改变原文的节点顺序。
"""

import pytest
import asyncio

from app.services.html_imitation_service import (
    analyze_html_for_imitation,
    render_html_imitation,
    replace_html_image_slots,
    select_html_image_slots,
)


@pytest.fixture(autouse=True)
def reset_test_tables():
    """覆盖全局数据库夹具。

    本文件只验证 HTML 纯函数，连接数据库既无必要，也会被无关的账号外键数据影响。
    """
    yield


REFERENCE_HTML = """
<section class="article-body" style="color:#333">
  <h2 style="color:#0a7">原始标题</h2>
  <p class="lead"><strong>原始导语</strong></p>
  <figure class="hero"><img src="https://example.com/original.jpg" alt="城市夜景" /></figure>
  <blockquote>原始引用</blockquote>
  <p>收尾段落</p>
</section>
"""


REFERENCE_HTML_WITH_CONTACT_SECTION = """
<article class="article-body">
  <h2>参考标题</h2>
  <p>这是一段必须参与仿写的正文。</p>
  <figure><img src="https://example.com/product.jpg" alt="产品场景" /></figure>
  <aside class="source-contact-card" style="padding:20px;background:#f6f6f6">
    <p>联系我们</p>
    <p>试坐优先，如需购买请通过产品详情或官方渠道咨询。</p>
    <p>绣蔓家具 TEL:18682130473</p>
    <img src="https://example.com/source-qrcode.png" alt="企业微信二维码" />
  </aside>
</article>
"""


def test_analyze_html_for_imitation_excludes_entire_reference_contact_section():
    """参考联系方式区域不能成为正文或图片槽位，避免被 Agent 继续仿写。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML_WITH_CONTACT_SECTION)

    assert [slot.original_text for slot in blueprint.text_slots] == [
        "参考标题",
        "这是一段必须参与仿写的正文。",
    ]
    assert [slot.source_url for slot in blueprint.image_slots] == [
        "https://example.com/product.jpg",
    ]
    assert "source-contact-card" not in blueprint.html_template
    assert "联系我们" not in blueprint.html_template
    assert "试坐优先" not in blueprint.html_template
    assert "18682130473" not in blueprint.html_template
    assert "source-qrcode.png" not in blueprint.html_template


def test_render_html_imitation_appends_only_configured_footer_after_reference_contact_removal():
    """固定页脚必须独立追加在末尾，不能复用参考联系方式卡片的样式或文字。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML_WITH_CONTACT_SECTION)

    result = render_html_imitation(
        blueprint,
        text_by_slot={
            "text-1": "新标题",
            "text-2": "新的完整正文",
        },
        image_by_slot={
            "image-1": {"keywords": "新品家具", "prompt": "新品家具场景"},
        },
        footer_template="绣蔓家具咨询：13800000000\n![企业微信二维码](https://cdn.example.com/my-qr.png)",
    )

    assert "source-contact-card" not in result.html
    assert "联系我们" not in result.html
    assert "试坐优先" not in result.html
    assert "18682130473" not in result.html
    assert "绣蔓家具咨询：13800000000" in result.html
    assert "https://cdn.example.com/my-qr.png" in result.html
    assert 'data-ai-footer-template="appended"' in result.html
    assert result.html.index("新的完整正文") < result.html.index("绣蔓家具咨询：13800000000")


def test_merge_does_not_append_footer_twice_when_html_renderer_already_appended_it():
    """HTML 仿写已追加固定页脚时，通用合并阶段不能再追加第二份。"""
    from app.schemas.article import ArticleState
    from app.services.article_agent_service import merge_images_into_content

    footer = "绣蔓家具咨询：13800000000\n![企业微信二维码](https://cdn.example.com/my-qr.png)"
    state = ArticleState(
        task_id="footer-appended-once",
        topic="测试",
        content=(
            '<article><p>正文</p></article>'
            '<div data-ai-footer-template="appended">'
            '<p>绣蔓家具咨询：13800000000</p>'
            '<img src="https://cdn.example.com/my-qr.png" />'
            "</div>"
        ),
        footer_template=footer,
    )

    result = merge_images_into_content(state)

    assert result.full_content.count("绣蔓家具咨询：13800000000") == 1
    assert result.full_content.count("https://cdn.example.com/my-qr.png") == 1


def test_analyze_html_for_imitation_keeps_structure_and_exposes_ordered_slots():
    """解析必须保留外层样式，并按 DOM 顺序产出文字和图片槽位。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML)

    assert blueprint.text_slots[0].tag_name == "h2"
    assert [slot.original_text for slot in blueprint.text_slots] == [
        "原始标题",
        "原始导语",
        "原始引用",
        "收尾段落",
    ]
    assert [slot.slot_id for slot in blueprint.image_slots] == ["image-1"]
    assert blueprint.image_slots[0].source_url == "https://example.com/original.jpg"
    assert 'class="article-body"' in blueprint.html_template
    assert 'style="color:#0a7"' in blueprint.html_template
    assert 'data-ai-text-slot="text-1"' in blueprint.html_template
    assert 'data-ai-image-slot="image-1"' in blueprint.html_template


def test_decorative_span_layout_keeps_inline_styles_and_static_separators():
    """角标与章节标题常用 span/strong，回填时必须保留各自的行内视觉样式。"""

    reference_html = """
    <section style="border:1px solid #b8a98f;padding:24px">
      <section style="text-align:right">
        <span style="display:inline-block;border-radius:50%;background:#1b1b1b;color:#fff">豪宅</span>
      </section>
      <section style="text-align:center">
        <strong style="font-size:28px;color:#222">艺术家私宅</strong>
      </section>
      <section style="text-align:center"><span style="color:#b8a98f">—</span></section>
      <section style="text-align:center">
        <span style="font-size:14px">住宅不只容纳生活</span>
      </section>
      <section style="margin-top:36px">
        <span style="background:#111;color:#fff;padding:3px 8px">不争而自在</span>
        <strong style="display:block;font-size:26px">一间属于自己的房间</strong>
        <span style="color:#c8b79b">▪</span>
        <p><span style="font-size:15px;color:#555">这里承载安静而完整的日常。</span></p>
      </section>
    </section>
    """

    blueprint = analyze_html_for_imitation(reference_html)

    assert [slot.original_text for slot in blueprint.text_slots] == [
        "豪宅",
        "艺术家私宅",
        "住宅不只容纳生活",
        "不争而自在",
        "一间属于自己的房间",
        "这里承载安静而完整的日常。",
    ]
    assert "—" in blueprint.html_template
    assert "▪" in blueprint.html_template

    result = render_html_imitation(
        blueprint,
        text_by_slot={
            "text-1": "私宅",
            "text-2": "设计师之家",
            "text-3": "空间回应真实的生活",
            "text-4": "松弛而有序",
            "text-5": "一间可以慢下来的房间",
            "text-6": "光线、材质与日常动作在这里自然相遇。",
        },
        image_by_slot={},
    )

    assert 'style="display:inline-block;border-radius:50%;background:#1b1b1b;color:#fff"' in result.html
    assert 'style="background:#111;color:#fff;padding:3px 8px"' in result.html
    assert 'style="display:block;font-size:26px"' in result.html
    assert 'style="font-size:15px;color:#555"' in result.html
    assert "私宅" in result.html
    assert "一间可以慢下来的房间" in result.html
    assert "艺术家私宅" not in result.html


def test_nested_inline_layout_keeps_deepest_text_style_after_rendering():
    """嵌套 span/strong 应把槽位下放到最深文字节点，避免回填清除内层样式。"""

    reference_html = """
    <section>
      <p style="text-align:center">
        <span style="display:inline-block;padding:4px 10px">
          <strong style="font-size:28px;color:#222">嵌套标题</strong>
        </span>
      </p>
    </section>
    """

    blueprint = analyze_html_for_imitation(reference_html)
    result = render_html_imitation(
        blueprint,
        text_by_slot={"text-1": "新的嵌套标题"},
        image_by_slot={},
    )

    assert blueprint.text_slots[0].tag_name == "strong"
    assert 'style="display:inline-block;padding:4px 10px"' in result.html
    assert 'style="font-size:28px;color:#222"' in result.html
    assert "新的嵌套标题" in result.html


def test_mixed_inline_layout_preserves_child_style_without_ghost_slots():
    """混合正文应分别回填直接文字和强调节点，不能清空子节点或创建幽灵槽位。"""

    reference_html = """
    <section>
      <p style="line-height:1.8">普通文字 <strong style="color:red">重点</strong> 结尾</p>
    </section>
    """

    blueprint = analyze_html_for_imitation(reference_html)
    assert [slot.original_text for slot in blueprint.text_slots] == [
        "普通文字",
        "重点",
        "结尾",
    ]

    result = render_html_imitation(
        blueprint,
        text_by_slot={
            "text-1": "新的开头",
            "text-2": "新的重点",
            "text-3": "新的结尾",
        },
        image_by_slot={},
    )

    assert 'style="color:red"' in result.html
    assert "新的开头" in result.html
    assert "新的重点" in result.html
    assert "新的结尾" in result.html
    assert "普通文字" not in result.html


def test_blueprint_prompt_payload_can_omit_unused_reference_image_urls_for_erp_mode():
    """ERP 图生图不参考投喂图时，提示词只保留槽位顺序，不能携带长签名 URL。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML)

    payload = blueprint.prompt_payload(include_source_urls=False)

    assert payload["image_slots"] == [{
        "id": "image-1",
        "position": 1,
        "reference_alt": "城市夜景",
    }]
    assert "original.jpg" not in str(payload)


def test_render_html_imitation_fills_only_matched_slots_and_creates_image_requirements():
    """模型文字只允许进入自身槽位，图片需求必须保持在原图片的位置。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML)

    result = render_html_imitation(
        blueprint,
        text_by_slot={
            "text-1": "新的标题",
            "text-2": "新的导语",
            "text-3": "新的引用",
            "text-4": "新的收尾",
        },
        image_by_slot={
            "image-1": {"keywords": "雨后城市夜景", "prompt": "公众号插图，雨后城市夜景"},
        },
    )

    assert "新的标题" in result.html
    assert "新的导语" in result.html
    assert "原始标题" not in result.html
    assert 'class="hero"' in result.html
    assert result.image_requirements[0].position == 1
    assert result.image_requirements[0].placeholder_id == "image-1"
    assert result.image_requirements[0].keywords == "雨后城市夜景"


def test_render_html_imitation_removes_trailing_connector_punctuation_from_headings_only():
    """标题末尾的连接性标点没有语义，段落中的正常标点必须保持不变。"""
    blueprint = analyze_html_for_imitation(
        "<h3>原小标题</h3><p>原段落</p>"
    )

    result = render_html_imitation(
        blueprint,
        text_by_slot={"text-1": "比例错了，", "text-2": "段落结尾，"},
        image_by_slot={},
    )

    assert "<h3>比例错了</h3>" in result.html
    assert "<p>段落结尾，</p>" in result.html


def test_render_html_imitation_removes_qrcode_region_without_configured_footer():
    """未配置固定底部内容时应删除整个二维码区域，不能遗留仿写联系方式。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML)

    result = render_html_imitation(
        blueprint,
        text_by_slot={slot.slot_id: "新内容" for slot in blueprint.text_slots},
        image_by_slot={"image-1": {"keywords": "不应生成", "prompt": "不应生成"}},
        excluded_image_slot_ids={"image-1"},
    )

    assert "<img" not in result.html
    assert "<figure" not in result.html
    assert result.image_requirements == ()


def test_html_image_limit_keeps_text_and_appends_fixed_footer_after_removing_contact_card():
    """只生成前五张产品图，参考联系卡删除后在末尾追加用户固定内容。"""

    reference_html = """
    <section class="article">
      <p>正文第一段</p>
      <figure class="image-one"><img src="https://example.com/1.jpg" /></figure>
      <p>正文第二段</p>
      <figure class="image-two"><img src="https://example.com/2.jpg" /></figure>
      <figure class="image-three"><img src="https://example.com/3.jpg" /></figure>
      <figure class="image-four"><img src="https://example.com/4.jpg" /></figure>
      <figure class="image-five"><img src="https://example.com/5.jpg" /></figure>
      <figure class="image-six"><img src="https://example.com/6.jpg" /></figure>
      <div class="contact-card" style="border:1px solid #ddd;padding:16px">
        <p>原电话：10086</p>
        <img src="https://example.com/source-qr.png" alt="二维码" />
      </div>
      <p>正文结尾</p>
    </section>
    """
    blueprint = analyze_html_for_imitation(reference_html)
    generated_slot_ids, empty_slot_ids = select_html_image_slots(
        blueprint,
        max_generated_images=5,
    )
    image_by_slot = {
        slot_id: {"keywords": f"产品场景{index}", "prompt": f"产品场景{index}"}
        for index, slot_id in enumerate(generated_slot_ids, start=1)
    }

    result = render_html_imitation(
        blueprint,
        text_by_slot={slot.slot_id: f"完整正文{slot.slot_id}" for slot in blueprint.text_slots},
        image_by_slot=image_by_slot,
        empty_image_slot_ids=empty_slot_ids,
        footer_template="电话：18682130473\n品牌：绣蔓家具\n![二维码](https://cdn.example.com/my-qr.png)",
    )

    assert len(result.image_requirements) == 5
    assert [item.placeholder_id for item in result.image_requirements] == list(generated_slot_ids)
    assert 'class="image-six"' in result.html
    assert 'class="image-six"></figure>' in result.html
    assert "完整正文text-1" in result.html
    assert "完整正文text-2" in result.html
    assert "完整正文text-3" in result.html
    assert "原电话：10086" not in result.html
    assert "电话：18682130473" in result.html
    assert "品牌：绣蔓家具" in result.html
    assert 'src="https://cdn.example.com/my-qr.png"' in result.html
    assert 'class="contact-card"' not in result.html
    assert 'data-ai-footer-template="appended"' in result.html


def test_merge_does_not_append_footer_twice_when_qrcode_card_consumed_it():
    """固定内容已写入参考卡片时，后处理不能在文章末尾再次追加一份。"""

    from app.schemas.article import ArticleState
    from app.services.article_agent_service import merge_images_into_content

    footer = "电话：18682130473\n![二维码](https://cdn.example.com/my-qr.png)"
    state = ArticleState(
        task_id="footer-consumed",
        topic="测试",
        content=(
            '<section><p>正文</p><div data-ai-footer-template="applied">'
            '<p>电话：18682130473</p><img src="https://cdn.example.com/my-qr.png" />'
            "</div></section>"
        ),
        footer_template=footer,
    )

    result = merge_images_into_content(state)

    assert result.full_content.count("电话：18682130473") == 1
    assert result.full_content.count("https://cdn.example.com/my-qr.png") == 1


def test_merge_removes_duplicate_title_from_first_html_paragraph_only():
    """旧入口若把标题写进 HTML 首段，后处理应删除该段但保留真正正文。"""
    from app.schemas.article import ArticleState, SelectedTitle
    from app.services.article_agent_service import merge_images_into_content

    title = "FSSF-2022422正在重新定义居家收纳的边界"
    state = ArticleState(
        task_id="html-leading-title-test",
        topic="居家收纳",
        title=SelectedTitle(main_title=title, sub_title=""),
        content=f"<section><p>{title}</p><p>这才是文章真正的开场正文。</p></section>",
    )

    result = merge_images_into_content(state)

    assert title not in result.full_content
    assert "这才是文章真正的开场正文。" in result.full_content
    assert result.full_content.count("<p>") == 1


def test_replace_html_image_slots_updates_existing_image_node_without_appending_gallery():
    """图片回填只能更新对应 img 的 src，不能把图片追加到文章末尾。"""
    blueprint = analyze_html_for_imitation(REFERENCE_HTML)
    rendered = render_html_imitation(
        blueprint,
        text_by_slot={slot.slot_id: "新内容" for slot in blueprint.text_slots},
        image_by_slot={"image-1": {"keywords": "城市", "prompt": "城市"}},
    )

    html = replace_html_image_slots(rendered.html, {"image-1": "https://cdn.example.com/new.jpg"})

    assert 'src="https://cdn.example.com/new.jpg"' in html
    assert html.count("<img") == 1
    assert html.index("<blockquote") > html.index("<img")


def test_merge_agent_images_replaces_html_slot_instead_of_appending_markdown_gallery():
    """现有图片 Agent 的结果必须能回填 HTML 槽位，不能退回 Markdown 追加策略。"""
    from app.schemas.article import ArticleState, ImageResult
    from app.services.article_agent_service import merge_images_into_content

    state = ArticleState(
        task_id="html-imitation-test",
        topic="测试主题",
        content='<p class="before">正文</p><figure><img data-ai-image-slot="image-1" src="__AI_IMAGE_SLOT_image-1__" /></figure><p>结尾</p>',
        images=[
            ImageResult(
                position=1,
                url="https://cdn.example.com/generated.jpg",
                method="DASHSCOPE",
                placeholder_id="image-1",
            )
        ],
    )

    merged = merge_images_into_content(state)

    assert 'src="https://cdn.example.com/generated.jpg"' in merged.full_content
    assert "data-ai-image-slot" not in merged.full_content
    assert merged.full_content.count("<img") == 1
    assert merged.full_content.index("<figure") < merged.full_content.index("结尾")


def test_html_content_agent_generates_text_and_image_requirements_from_the_same_blueprint(monkeypatch):
    """HTML 内容 Agent 必须只返回槽位内容，并复用参考图理解结果指导新图生成。"""
    from app.schemas.article import ArticleState, OutlineResult, OutlineSection, SelectedTitle
    from app.services.article_agent_service import agent3_generate_html_imitation_content
    import app.services.article_agent_service as article_agent_service
    import app.agent.nodes.image_understanding_node as image_understanding_node

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        assert "text_slots" in user_message
        assert "雨后街道" in user_message
        return """{
          "text_slots": [
            {"id": "text-1", "content": "新的标题"},
            {"id": "text-2", "content": "新的导语"},
            {"id": "text-3", "content": "新的引用"},
            {"id": "text-4", "content": "新的收尾"}
          ],
          "image_slots": [
            {"id": "image-1", "keywords": "雨后街道", "prompt": "雨后城市街道插图"}
          ]
        }"""

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        image_understanding_node,
        "understand_images",
        lambda urls: [{"subject": "雨后街道", "visual_style": "电影感", "is_qrcode": False}],
    )
    state = ArticleState(
        task_id="html-agent-test",
        topic="城市漫步",
        title=SelectedTitle(main_title="新标题", sub_title=""),
        outline=OutlineResult(sections=[OutlineSection(section=1, title="开头", points=["切入主题"])]),
        reference_html=REFERENCE_HTML,
    )

    result = asyncio.run(agent3_generate_html_imitation_content(state))

    assert "新的标题" in result.content
    assert "原始标题" not in result.content
    assert 'data-ai-image-slot="image-1"' in result.content
    assert result.image_requirements[0].keywords == "雨后街道"
    assert result.image_requirements[0].prompt
    assert "雨后街道" in result.image_requirements[0].prompt
    assert "电影感" in result.image_requirements[0].prompt


def test_erp_html_imitation_skips_reference_image_understanding(monkeypatch):
    """ERP 产品图生图只保留投喂源版式，不能读取参考文章图片作为视觉输入。"""
    from app.schemas.article import ArticleState, OutlineResult, OutlineSection, SelectedTitle
    from app.services.article_agent_service import agent3_generate_html_imitation_content
    import app.services.article_agent_service as article_agent_service
    import app.agent.nodes.image_understanding_node as image_understanding_node

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        assert "雨后街道" not in user_message
        # ERP 路径不读取投喂源图片，但必须在这一次文本调用中接收完整背景规则。
        # 后续每张图生图将复用 Agent 产出的槽位提示词，避免重复传入知识库。
        assert "图片背景知识库" in user_message
        assert "墨绿、古铜金" in user_message
        return '''{
          "text_slots": [
            {"id": "text-1", "content": "新的标题"},
            {"id": "text-2", "content": "新的导语"},
            {"id": "text-3", "content": "新的引用"},
            {"id": "text-4", "content": "新的收尾"}
          ],
          "image_slots": [
            {"id": "image-1", "keywords": "异形子母茶几", "prompt": "暖色现代客厅"}
          ]
        }'''

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)

    def fail_if_called(urls):
        raise AssertionError("ERP 图文仿写不应调用参考图片理解")

    monkeypatch.setattr(image_understanding_node, "understand_images", fail_if_called)
    state = ArticleState(
        task_id="html-erp-no-visual-reference",
        topic="异形子母茶几",
        product_name="异形子母茶几",
        title=SelectedTitle(main_title="异形子母茶几的客厅答案", sub_title=""),
        outline=OutlineResult(sections=[OutlineSection(section=1, title="开头", points=["切入主题"])]),
        reference_html=REFERENCE_HTML,
        skip_reference_image_understanding=True,
        image_prompt_context="背景使用墨绿、古铜金与低饱和现代客厅，保留产品主体。",
    )

    result = asyncio.run(agent3_generate_html_imitation_content(state))

    assert result.image_requirements[0].keywords == "异形子母茶几"
    assert "暖色现代客厅" in result.image_requirements[0].prompt


def test_erp_image_generation_uses_slot_prompt_without_repeating_knowledge_context(monkeypatch):
    """ERP 图生图应消费槽位 Agent 的最终提示词，而非逐张重复背景知识库。"""
    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_generation_models import GeneratedImage
    from app.services.image_generation_service import image_generation_service

    captured_requests = []

    async def fake_generate(request):
        captured_requests.append(request)
        return GeneratedImage(
            url="https://cdn.example.com/generated.png",
            provider="test-provider",
            model="test-model",
        )

    monkeypatch.setattr(image_generation_service, "generate", fake_generate)
    state = ArticleState(
        task_id="erp-slot-prompt-only",
        tenant_id=107,
        topic="异形子母茶几",
        product_name="异形子母茶几",
        reference_html=REFERENCE_HTML,
        reference_image_url="https://cdn.example.com/product.png",
        # 此文本模拟较长知识库。它只能进入一次槽位内容 Agent，不能重复进入图片请求。
        image_prompt_context="这是不应逐张重复的完整品牌背景知识库。",
        image_requirements=[
            ImageRequirement(
                position=1,
                type="inline",
                image_source="DASHSCOPE",
                keywords="异形子母茶几",
                prompt="低饱和现代客厅，墨绿与古铜金，保留产品主体。",
                placeholder_id="image-1",
            )
        ],
    )

    result = asyncio.run(agent5_generate_images(state))

    assert len(captured_requests) == 1
    assert "低饱和现代客厅" in captured_requests[0].prompt
    assert "目标产品：异形子母茶几" in captured_requests[0].prompt
    assert "品牌视觉约束" not in captured_requests[0].prompt
    assert "不应逐张重复" not in captured_requests[0].prompt
    assert result.images[0].placeholder_id == "image-1"


def test_erp_html_image_generation_rejects_slot_without_complete_visual_prompt(monkeypatch):
    """HTML ERP 槽位缺少完整视觉提示词时必须停止，不能退化为仅产品关键词。"""
    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent5_generate_images
    from app.services.image_generation_service import image_generation_service

    async def fail_if_called(request):
        raise AssertionError("缺少槽位视觉提示词时不应调用图生图模型")

    monkeypatch.setattr(image_generation_service, "generate", fail_if_called)
    state = ArticleState(
        task_id="erp-slot-prompt-required",
        tenant_id=107,
        topic="异形子母茶几",
        product_name="异形子母茶几",
        reference_html=REFERENCE_HTML,
        reference_image_url="https://cdn.example.com/product.png",
        image_requirements=[
            ImageRequirement(
                position=1,
                type="inline",
                image_source="DASHSCOPE",
                keywords="异形子母茶几",
                prompt="",
                placeholder_id="image-1",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="缺少完整视觉提示词"):
        asyncio.run(agent5_generate_images(state))


def test_html_content_agent_repairs_title_returned_as_opening_paragraph(monkeypatch):
    """首段误生成文章标题时，只重写异常槽位，不能直接删除并损失开场正文。"""
    from app.schemas.article import ArticleState, OutlineResult, OutlineSection, SelectedTitle
    from app.services.article_agent_service import agent3_generate_html_imitation_content
    import app.services.article_agent_service as article_agent_service

    model_calls = []

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        """第一次模拟错误正文，第二次返回修复后的开场段落。"""
        model_calls.append(user_message)
        if len(model_calls) == 1:
            return """{
              "text_slots": [
                {"id": "text-1", "content": "FSSF-2022422正在重新定义居家收纳的边界"},
                {"id": "text-2", "content": "收纳真正影响的是日常使用节奏"}
              ],
              "image_slots": []
            }"""
        assert "text-1" in user_message
        assert "不能重复文章标题" in user_message
        return """{
          "text_slots": [
            {"id": "text-1", "content": "整理客厅时，人们常常先增加柜体，却忽略了拿取路径和日常习惯。真正需要调整的，是家具与生活动作之间的关系。"}
          ]
        }"""

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    state = ArticleState(
        task_id="html-title-repair-test",
        topic="居家收纳",
        product_name="FSSF-2022422",
        title=SelectedTitle(
            main_title="FSSF-2022422正在重新定义居家收纳的边界",
            sub_title="",
        ),
        outline=OutlineResult(
            sections=[OutlineSection(section=1, title="开头", points=["从真实使用场景切入"])]
        ),
        reference_html=(
            "<section><p>装修客厅时的真实困扰与选择过程，需要完整说明。</p>"
            "<h2>原小标题</h2></section>"
        ),
    )

    result = asyncio.run(agent3_generate_html_imitation_content(state))

    assert len(model_calls) == 2
    assert "整理客厅时" in result.content
    assert "FSSF-2022422正在重新定义居家收纳的边界" not in result.content
    assert "收纳真正影响的是日常使用节奏" in result.content


def test_html_content_agent_excludes_image_slots_identified_as_qrcodes(monkeypatch):
    """二维码不得保留在 HTML 蓝图，也不得形成后续图片生成任务。"""
    from app.schemas.article import ArticleState, OutlineResult, OutlineSection, SelectedTitle
    from app.services.article_agent_service import agent3_generate_html_imitation_content
    import app.services.article_agent_service as article_agent_service
    import app.agent.nodes.image_understanding_node as image_understanding_node

    async def fake_call_llm(system_prompt, user_message, **kwargs):
        assert '"image_slots": []' in user_message
        return """{
          "text_slots": [
            {"id": "text-1", "content": "新的标题"},
            {"id": "text-2", "content": "新的导语"},
            {"id": "text-3", "content": "新的引用"},
            {"id": "text-4", "content": "新的收尾"}
          ],
          "image_slots": []
        }"""

    monkeypatch.setattr(article_agent_service, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        image_understanding_node,
        "understand_images",
        lambda urls: [{"subject": "二维码", "is_qrcode": True}],
    )
    state = ArticleState(
        task_id="html-qrcode-test",
        topic="城市漫步",
        title=SelectedTitle(main_title="新标题", sub_title=""),
        outline=OutlineResult(sections=[OutlineSection(section=1, title="开头", points=["切入主题"])]),
        reference_html=REFERENCE_HTML,
    )

    result = asyncio.run(agent3_generate_html_imitation_content(state))

    assert "<img" not in result.content
    assert result.image_requirements == []


def test_image_requirement_agent_keeps_html_slot_requirements(monkeypatch):
    """HTML 仿写已生成的图片需求不能被旧 Markdown 图片分析覆盖。"""
    from app.schemas.article import ArticleState, ImageRequirement
    from app.services.article_agent_service import agent4_analyze_image_requirements
    import app.services.article_agent_service as article_agent_service

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("HTML 槽位模式不应再次调用通用图片需求分析")

    monkeypatch.setattr(article_agent_service, "_call_llm", fail_if_called)
    state = ArticleState(
        task_id="html-image-requirement-test",
        topic="测试主题",
        content='<figure><img data-ai-image-slot="image-1" src="__AI_IMAGE_SLOT_image-1__" /></figure>',
        image_requirements=[
            ImageRequirement(
                position=1,
                type="inline",
                image_source="DASHSCOPE",
                keywords="雨后街道",
                placeholder_id="image-1",
            )
        ],
    )

    result = asyncio.run(agent4_analyze_image_requirements(state))

    assert result.image_requirements[0].placeholder_id == "image-1"
