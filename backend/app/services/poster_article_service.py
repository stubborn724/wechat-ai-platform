"""ERP 产品驱动的纯海报文章编排服务。

该服务把“图片内文案”和“图片视觉规则”作为一个不可拆分的输入交给生图服务。
这样不会出现正文 Agent 写了一套内容、图片 Agent 只收到关键词而丢失品牌格式的
问题；固定二维码始终由 HTML 页脚注入，不交给任何模型绘制。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.services.image_generation_models import ImageGenerationRequest
from app.services.publication_format_service import PublicationFormatProfile
from app.services.scheduled_product_scene_service import (
    ProductSceneProfile,
    append_product_scene_guard,
)
from app.services.scheduled_image_quality_service import (
    append_low_information_retry_instruction,
)
from app.services.text_generation_service import TextGenerationRequest, text_generation_service
from app.services.writing_style_template_service import (
    get_writing_style_template_prompt,
    get_writing_style_template_title_max_chars,
)


@dataclass(frozen=True)
class PosterText:
    """单张海报的画面场景和必须逐字嵌入画面的文案。"""

    copy: str
    scene: str


@dataclass(frozen=True)
class PosterPlan:
    """纯海报文章的元标题及顺序固定的海报文案集合。"""

    article_title: str
    posters: tuple[PosterText, ...]


async def generate_poster_plan(
    *,
    profile: PublicationFormatProfile,
    product_name: str,
    style: str | None = None,
    body_copy_only: bool = False,
    complete_text: Callable[[TextGenerationRequest], Awaitable[str]] = text_generation_service.complete,
) -> PosterPlan:
    """生成公众号标题与顺序固定的海报正文文案，并严格校验输出数量。

    ERP 产品名称同时承担公众号标题的语义锚点。图片内文案仍然不能出现型号，
    但草稿标题必须保留产品名称或明确品类，避免出现与产品无关的抽象标题。
    ``body_copy_only`` 专用于新三图程序叠字模板：三个画面都承担正文叙事，
    不能沿用历史“标题海报 + 内容海报”的混合角色，避免出现只有四五个字的
    空白首图或收尾图。
    """

    required_count = profile.poster_count + 1
    style_prompt = get_writing_style_template_prompt(style)
    # 海报是品牌化长标题，不应继续沿用普通图文的十二字上限。若任务明确选了
    # 写作模板，则仍可读取模板自带的推荐长度；否则统一按海报专用长标题处理。
    title_max_chars = max(
        26,
        get_writing_style_template_title_max_chars(style) or 0,
        profile.title_max_chars or 0,
    )
    request = TextGenerationRequest(
        system_prompt=(
            "你是高端家居品牌的海报文案策划。只返回 JSON，不使用 Markdown。"
            "文案应克制、有意境，绝不包含二维码、品牌 logo 或促销号召。"
            "公众号标题必须包含目标产品名称或明确产品品类。"
        ),
        user_message=f"""根据以下完整品牌发布规范生成纯海报拼接方案。

目标产品：{product_name}
公众号草稿标题必须自然包含目标产品名称或明确产品品类，不能使用和产品无关的潮流词。
图片内是否允许显示产品型号：否，必须不得出现产品型号或产品名称。
需要生成：{required_count} 张{"正文型内容海报" if body_copy_only else "海报，其中第 1 张为标题海报，其余为内容海报"}。
标题最大 {title_max_chars} 字；每张内容海报文案最多 {profile.copy_max_chars} 字。
{"每张海报都必须是 2-3 句、带标点的正文型内容，适合自动分成多行排版；禁止只写标题、口号或四字短句。" if body_copy_only else "第 1 张标题海报可使用简短视觉标题。"}

{style_prompt}

完整发布规范（不可删减）：
{profile.raw_directives}

严格返回如下 JSON：
{{
  "article_title": "用于公众号草稿的简短标题",
  "posters": [
    {{"copy": "每张海报的正文型内嵌文字", "scene": "对应的空间或细节场景"}}
  ]
}}""",
        temperature=0.65,
    )
    raw = await complete_text(request)
    data = _parse_json(raw)
    title = _clean_copy(data.get("article_title", ""), title_max_chars)
    raw_posters = data.get("posters") if isinstance(data.get("posters"), list) else []

    posters: list[PosterText] = []
    if not body_copy_only:
        title_poster_copy = _clean_copy(data.get("title_poster_copy", ""), profile.title_max_chars)
        posters.append(PosterText(
            copy=title_poster_copy or title or "东意西形",
            scene="标题海报",
        ))

    expected_model_count = required_count if body_copy_only else profile.poster_count
    for index, item in enumerate(raw_posters[:expected_model_count], start=1):
        item = item if isinstance(item, dict) else {}
        copy = _clean_copy(item.get("copy", ""), profile.copy_max_chars)
        scene = _clean_copy(item.get("scene", ""), 80)
        if copy:
            # 内容图承担文章叙事，不能把“透空诗境”这类标题式短语当作正文。
            # 这层确定性校验只处理不合格结果，正常的品牌化文案完整保留。
            if not _is_story_copy(copy):
                copy = _build_story_copy(product_name, index)
            posters.append(PosterText(
                copy=copy,
                scene=scene or (
                    f"内容海报 {index}" if body_copy_only else f"意境海报 {index}"
                ),
            ))

    # 模型偶尔少返回一两张时，宁可用已验证的中性短句补足，也不能改变文章结构。
    while len(posters) < required_count:
        index = len(posters)
        posters.append(PosterText(
            copy=_build_story_copy(product_name, index),
            scene=f"内容海报 {index}" if body_copy_only else f"意境海报 {index}",
        ))

    forbidden_product_name = str(product_name or "").strip()
    if forbidden_product_name:
        posters = [
            PosterText(
                copy=item.copy.replace(forbidden_product_name, "").strip("，。；、 "),
                scene=item.scene,
            )
            for item in posters
        ]
    safe_title = _ensure_product_related_title(
        title,
        product_name=forbidden_product_name,
        max_chars=title_max_chars,
    )
    return PosterPlan(
        article_title=safe_title,
        posters=tuple(posters[:required_count]),
    )


async def generate_poster_images(
    *,
    profile: PublicationFormatProfile,
    plan: PosterPlan,
    product_name: str,
    tenant_id: int,
    reference_image_bytes: bytes | None,
    reference_content_type: str | None,
    product_scene_profile: ProductSceneProfile | None = None,
    generate_image: Callable[[ImageGenerationRequest], Awaitable[object]],
    # 新三品牌模板由程序叠加中文文案；旧任务默认仍让模型按历史提示词绘制，
    # 通过默认值把新行为限制在显式模板开关内。
    embed_copy_in_model: bool = True,
    quality_checker: Callable[[str], Awaitable[object]] | None = None,
) -> list[str]:
    """逐张图生图，确保产品主体、功能空间和品牌视觉规则同时生效。

    纯海报以前已经复用了 ERP 原图，但产品场景快照只进入普通 HTML 图文链路，
    使海报模型仍可能把餐桌放入客厅。这里把同一份确定性场景规则传入每张海报，
    并保留参考图字节作为唯一真实主体来源，解决“只生成朦胧背景”的退化结果。
    """

    if not reference_image_bytes:
        raise ValueError("纯海报任务缺少 ERP 产品参考图")
    visual_anchor = _build_shared_visual_anchor(profile.visual_directives)

    # 程序叠字模式也必须生成三张独立机位。早期的单主视觉切片虽然省一次调用，
    # 但会把同一个产品角度裁成三段，无法形成真实的产品叙事。共同视觉锚点和
    # 归档合成器负责保持连续感，三张图则负责提供空间、主视觉、细节三种视角。
    if not embed_copy_in_model:
        return await _generate_programmatic_three_view_images(
            profile=profile,
            plan=plan,
            product_name=product_name,
            tenant_id=tenant_id,
            reference_image_bytes=reference_image_bytes,
            reference_content_type=reference_content_type,
            visual_anchor=visual_anchor,
            product_scene_profile=product_scene_profile,
            generate_image=generate_image,
            quality_checker=quality_checker,
        )

    urls: list[str] = []
    for index, poster in enumerate(plan.posters, start=1):
        prompt = _build_poster_prompt(
            profile=profile,
            product_name=product_name,
            poster=poster,
            position=index,
            visual_anchor=visual_anchor,
            product_scene_profile=product_scene_profile,
            embed_copy_in_model=embed_copy_in_model,
        )
        final_prompt = prompt
        generated = None
        quality_report = None
        # 质量检查是可注入的：生产执行器传入真实下载检查，单元测试和旧调用点
        # 不会凭空访问外网。最多补生成一次，避免低质量结果和无限重试并存。
        for attempt in range(2 if quality_checker is not None else 1):
            generated = await generate_image(ImageGenerationRequest(
                prompt=final_prompt,
                size="1024*1536",
                tenant_id=tenant_id,
                reference_image_bytes=reference_image_bytes,
                reference_content_type=reference_content_type or "image/jpeg",
                # 文案是否由程序叠加不改变产品参考图的传递；该字段也让支持它
                # 的供应商明确不要自行绘制文字。
                no_text=not embed_copy_in_model,
            ))
            url = str(getattr(generated, "url", generated) or "").strip()
            if not url:
                raise RuntimeError(f"第 {index} 张海报未返回图片地址")
            if quality_checker is None:
                break

            quality_report = await quality_checker(url)
            if bool(getattr(quality_report, "is_usable", False)):
                break
            if attempt == 0:
                final_prompt = append_low_information_retry_instruction(final_prompt)

        if (
            quality_checker is not None
            and (
                generated is None
                or quality_report is None
                or not bool(getattr(quality_report, "is_usable", False))
            )
        ):
            reason = getattr(quality_report, "reason", "未完成质量检查")
            raise RuntimeError(
                f"第 {index} 张海报连续两次质量检查未通过，{reason}"
            )
        url = str(getattr(generated, "url", generated) or "").strip()
        urls.append(url)
    return urls


async def _generate_programmatic_three_view_images(
    *,
    profile: PublicationFormatProfile,
    plan: PosterPlan,
    product_name: str,
    tenant_id: int,
    reference_image_bytes: bytes,
    reference_content_type: str | None,
    visual_anchor: str,
    product_scene_profile: ProductSceneProfile | None,
    generate_image: Callable[[ImageGenerationRequest], Awaitable[object]],
    quality_checker: Callable[[str], Awaitable[object]] | None,
) -> list[str]:
    """生成同产品的三种机位，供连续海报合成器做无缝连接。

    该函数只由 ``programmatic_text_v1`` 调用。旧海报继续走原有逐图提示词，
    以免已上线的绣蔓任务因视觉策略切换而发生不可预期变化。
    """

    urls: list[str] = []
    for index, poster in enumerate(plan.posters, start=1):
        prompt = _build_poster_prompt(
            profile=profile,
            product_name=product_name,
            poster=poster,
            position=index,
            visual_anchor=visual_anchor,
            product_scene_profile=product_scene_profile,
            embed_copy_in_model=False,
        )
        prompt += f"\n{_build_programmatic_view_instruction(index)}"
        generated = None
        quality_report = None
        final_prompt = prompt
        for attempt in range(2 if quality_checker is not None else 1):
            generated = await generate_image(ImageGenerationRequest(
                prompt=final_prompt,
                size="1024*1536",
                tenant_id=tenant_id,
                reference_image_bytes=reference_image_bytes,
                reference_content_type=reference_content_type or "image/jpeg",
                no_text=True,
            ))
            url = str(getattr(generated, "url", generated) or "").strip()
            if not url:
                raise RuntimeError(f"第 {index} 张三视角海报未返回图片地址")
            if quality_checker is None:
                break
            quality_report = await quality_checker(url)
            if bool(getattr(quality_report, "is_usable", False)):
                break
            if attempt == 0:
                final_prompt = append_low_information_retry_instruction(final_prompt)
        if quality_checker is not None and (
            generated is None
            or quality_report is None
            or not bool(getattr(quality_report, "is_usable", False))
        ):
            reason = getattr(quality_report, "reason", "未完成质量检查")
            raise RuntimeError(f"第 {index} 张三视角海报质量检查未通过，{reason}")
        urls.append(str(getattr(generated, "url", generated) or "").strip())
    return urls


def _build_poster_prompt(
    *,
    profile: PublicationFormatProfile,
    product_name: str,
    poster: PosterText,
    position: int,
    visual_anchor: str,
    product_scene_profile: ProductSceneProfile | None,
    embed_copy_in_model: bool,
) -> str:
    """合成每张海报的不可省略视觉、主体和文字约束。"""

    copy_instruction = (
        f"必须在画面上半部居中、清晰且完整地嵌入以下中文文案，逐字准确：\n"
        f"「{poster.copy}」"
        if embed_copy_in_model
        else (
            "海报文案由程序叠加到图片归档结果中。模型不要生成任何可辨认的中文、"
            "英文字母、数字、品牌 logo 或水印，只需为画面上半部保留干净、连续、"
            "有呼吸感的文字安全区。"
        )
    )
    prompt = f"""这是第 {position} 张竖版长海报。以参考图中的 ERP 产品为唯一真实主体，
保持产品的材质、比例、结构和主体关系，不替换、不变形。产品名称仅供理解：{product_name}，
禁止把产品名称或型号写入画面。

参考图中的 ERP 产品必须清晰可见、占据明确主体位置，保留真实轮廓、材质纹理和关键结构；
不能只生成朦胧光影、空白墙面或通用家居背景，不能把产品虚化到无法辨认，不能用其他家具
替换参考图主体。背景可以柔和、低饱和和有景深，但必须是围绕该真实产品构建的可识别功能空间。

【通用朦胧海报质感】
整体采用参考海报式的轻柔朦胧摄影质感：低对比度、低饱和度、柔和漫射光、轻微薄雾、
浅景深和透光感；背景与产品之间有自然空气层次，画面边缘可以柔焦，但产品主体轮廓、
关键材质和使用关系必须清楚。朦胧是柔化光线和空间氛围，不是空白、纯色、过度高斯模糊，
也不是把产品遮掉。所有海报保持同一色温、光线方向和雾化程度，只改变镜头和场景细节。

{visual_anchor}

画面场景：{poster.scene}
{copy_instruction}

完整图片视觉规范（不可省略）：
{profile.visual_directives}

完整文案规范（不可省略）：
{profile.copy_directives}

    硬性限制：只生成一张竖版长海报；不要二维码、不要额外文字、
    不要水印以外的联系方式，不要改变产品主体。"""
    if product_scene_profile is not None:
        prompt = append_product_scene_guard(
            prompt,
            product_scene_profile,
            product_name=product_name,
        )
    return prompt


def _build_programmatic_view_instruction(position: int) -> str:
    """定义三图海报的镜头分工，确保同产品而非同一张图片的重复裁切。"""

    view_instructions = {
        1: "【本图机位】空间引入广角：从产品所在功能空间的自然视角进入，产品清晰可见但不占满画面，保留上半部文字留白。",
        2: "【本图机位】完整产品主视觉：使用三分之四角度或正面视角，完整呈现产品轮廓、比例和主要材质，作为整组的视觉中心。",
        3: "【本图机位】材质、结构、局部细节或侧后角度：展示桌边、支撑、纹理、五金或产品与使用场景的细节，必须与前两张构图明显不同。",
    }
    instruction = view_instructions.get(position, "【本图机位】使用与前图不同的产品细节或生活视角。")
    return (
        "【三视角连续叙事】本篇三张图必须是同一 ERP 产品、同一房间、同色温、同光向和同一朦胧层次，"
        "但每张的镜头角度、焦距和构图必须不同；不能复制、镜像、裁切或仅放大前一张。\n"
        f"{instruction}"
    )


def _build_shared_visual_anchor(visual_directives: str) -> str:
    """把知识库视觉规则提升为整篇海报共用的背景契约。

    每张海报仍然独立生成，但背景规则不能随场景变化而重新发散。这里明确
    “保持不变”和“允许变化”的边界，让模型只改变镜头关注点与文案，不改变
    品牌空间、色板、光线、材质和文字安全区。规则原文完整保留，避免摘要丢失
    用户在知识库里配置的特殊背景要求。
    """

    directives = str(visual_directives or "").strip()
    return f"""【本篇统一背景视觉锚点】
以下规则适用于本篇所有海报，必须保持为同一套视觉系统：品牌空间类型、背景氛围、
主色与辅助色、光线方向和色温、材质表现、镜头语言、景深关系以及文字安全区均保持一致。
每张图只允许改变当前场景的关注点、产品展示角度和对应文案，不得另起一套背景风格，
不得把不同品牌或不同色系的家居空间混入同一篇文章。

知识库背景规则（必须逐条遵守）：
{directives}"""


def _parse_json(raw: str) -> dict:
    """容忍模型的 Markdown 围栏和前后说明，只提取最外层 JSON 对象。"""

    normalized = str(raw or "").strip()
    match = re.search(r"\{.*\}", normalized, re.DOTALL)
    if not match:
        raise ValueError("海报文案 Agent 未返回 JSON")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("海报文案 Agent 返回的 JSON 无效") from exc
    if not isinstance(parsed, dict):
        raise ValueError("海报文案 Agent 返回格式错误")
    return parsed


def _clean_copy(value: object, max_chars: int) -> str:
    """清理模型多余空白并守住发布规范的文案长度上限。"""

    text = re.sub(r"\s+", "", str(value or "")).strip("“”\"'")
    return text[:max_chars]


_PRODUCT_CATEGORY_PATTERNS = (
    "餐桌", "茶几", "书桌", "边几", "吧台", "沙发", "椅", "床", "柜", "屏风", "灯", "几",
)


def _product_anchor(product_name: str) -> str:
    """从 ERP 名称提取稳定品类词，供标题校验和兜底文案使用。"""

    normalized = re.sub(r"\s+", "", str(product_name or "")).strip()
    for category in _PRODUCT_CATEGORY_PATTERNS:
        if category in normalized:
            return category
    return normalized[:12] or "家居产品"


def _ensure_product_related_title(title: str, *, product_name: str, max_chars: int) -> str:
    """为公众号标题补齐产品语义，并稳定使用“产品名|风格长句”格式。

    纯海报的公众号标题不是普通图文标题，而是品牌化的海报总题。它必须让读者
    先看到真实产品，再看到风格长句，因此这里不再用“产品品类·短标题”兜底。
    这样即使模型返回一段不稳定文本，最终草稿仍保留用户期望的标题结构。
    """

    normalized = _clean_copy(title, max_chars * 2)
    product_label = _poster_title_subject(product_name)
    anchor = _product_anchor(product_name)

    # 模型返回的标题只要还保留完整语义，就按“产品名|风格长句”结构保留；
    # 一旦出现型号、夸张营销词或明显跑偏词，就直接回退为稳定的品牌长句。
    normalized = normalized.strip("｜|")
    normalized = re.sub(r"^[^｜|·:：]+[｜|·:：]\s*", "", normalized)
    if not normalized or _is_model_identifier_title(normalized) or any(
        marker in normalized for marker in ("曲奇", "潮流中", "我们向", "爆款", "震撼")
    ):
        normalized = "东方神韵与当代奢雅，在沉静里修养日常"
    normalized = normalized.replace(product_label, "").strip("，。；、:： ")
    candidate = _clean_copy(f"{product_label}|{normalized}", max_chars)
    if "|" not in candidate:
        fallback = _clean_copy(f"{product_label}|东方神韵与当代奢雅，在沉静里修养日常", max_chars)
        return fallback or f"{product_label}|留住生活里的从容"
    return candidate


def _is_model_identifier_title(title: str) -> bool:
    """识别模型号、SKU 等不能直接作为公众号标题的低语义输出。

    ERP 产品名允许包含型号，但草稿标题不能退化为“沙发·fssf20198150”。
    此处只拦截中文语义不足且夹杂字母数字的结果，保留正常的中文产品标题，
    避免对品牌英文名或有意义的正文表达做过度清洗。
    """

    normalized = str(title or "").strip()
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    return bool(re.search(r"[A-Za-z0-9]", normalized) and chinese_count < 4)


def _build_product_title_fallback(anchor: str) -> str:
    """为无效模型标题生成与产品品类匹配的可发布兜底标题。"""

    title_by_category = {
        "沙发": "东方神韵与当代奢雅，在沉静里修养日常",
        "餐桌": "东方神韵与当代奢雅，在沉静里修养日常",
        "茶几": "东方神韵与当代奢雅，在沉静里修养日常",
        "椅": "东方神韵与当代奢雅，在沉静里修养日常",
        "床": "东方神韵与当代奢雅，在沉静里修养日常",
        "柜": "东方神韵与当代奢雅，在沉静里修养日常",
        "灯": "东方神韵与当代奢雅，在沉静里修养日常",
    }
    return title_by_category.get(anchor, "东方神韵与当代奢雅，在沉静里修养日常")


def _poster_title_subject(product_name: str) -> str:
    """提取海报标题中应优先展示的产品主体，避免模型编号污染标题。"""

    normalized = _clean_copy(product_name, 24)
    if not normalized:
        return _product_anchor(product_name) or "家居产品"

    # ERP 名称有时会把型号直接粘在中文品类前面，例如
    # ``FSCJ3012家具单品·家具单品``。先截取型号后的中文片段，避免可读标题
    # 把内部 SKU 带到公众号；没有匹配时再走下面的通用中文片段策略。
    suffix_match = re.search(
        r"(?:[A-Za-z]{2,}\d{3,}|\d{4,})(?P<label>[\u4e00-\u9fff][\u4e00-\u9fff·、]*)",
        normalized,
    )
    if suffix_match:
        suffix_label = suffix_match.group("label").strip("·、")
        if suffix_label:
            return suffix_label.split("·", 1)[0]

    segments = [
        segment.strip()
        for segment in re.split(r"[·|｜/:：_\-\s]+", normalized)
        if segment and segment.strip()
    ]
    chinese_segments = [segment for segment in segments if re.search(r"[\u4e00-\u9fff]", segment)]
    if chinese_segments:
        for segment in chinese_segments:
            if _product_anchor(product_name) and _product_anchor(product_name) in segment:
                return segment
        return max(chinese_segments, key=len)

    anchor = _product_anchor(product_name)
    return anchor or normalized


def _is_story_copy(copy: str) -> bool:
    """判断内容海报是否具备正文型信息量，而不是一个版面标题。"""

    normalized = _clean_copy(copy, 160)
    return len(normalized) >= 16 and any(mark in normalized for mark in "，。；！？")


def _build_story_copy(product_name: str, position: int) -> str:
    """为缺失或标题式短文案提供中性正文兜底，不覆盖正常品牌文案。"""

    anchor = _product_anchor(product_name)
    templates = (
        f"{anchor}安放在日常空间里，让材质、比例与光线慢慢形成舒服的秩序。",
        f"围坐、停留与交流，都从{anchor}清晰的结构和恰当的尺度自然开始。",
        f"从边缘到触感，{anchor}把每一次使用的细节，安静地留在生活里。",
    )
    return templates[(max(1, position) - 1) % len(templates)]
