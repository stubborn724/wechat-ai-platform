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
from app.services.text_generation_service import TextGenerationRequest, text_generation_service


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
    complete_text: Callable[[TextGenerationRequest], Awaitable[str]] = text_generation_service.complete,
) -> PosterPlan:
    """生成标题海报与内容海报文案，并严格校验输出数量。

    ERP 产品名称只用于让模型理解产品和画面主体；当品牌规范要求“不显示产品型号”
    时，程序会在回填前删除产品名称，避免文字规则被模型偶然违反。
    """

    required_count = profile.poster_count + 1
    request = TextGenerationRequest(
        system_prompt=(
            "你是高端家居品牌的海报文案策划。只返回 JSON，不使用 Markdown。"
            "文案应克制、有意境，绝不包含二维码、品牌 logo 或促销号召。"
        ),
        user_message=f"""根据以下完整品牌发布规范生成纯海报拼接方案。

目标产品：{product_name}
图片内是否允许显示产品型号：否，必须不得出现产品型号或产品名称。
需要生成：1 张标题海报 + {profile.poster_count} 张内容海报。
标题最大 {profile.title_max_chars} 字；每张内容海报文案最多 {profile.copy_max_chars} 字。

完整发布规范（不可删减）：
{profile.raw_directives}

严格返回如下 JSON：
{{
  "article_title": "用于公众号草稿的简短标题",
  "title_poster_copy": "标题海报内嵌文字",
  "posters": [
    {{"copy": "内容海报内嵌文字", "scene": "对应的空间或细节场景"}}
  ]
}}""",
        temperature=0.65,
    )
    raw = await complete_text(request)
    data = _parse_json(raw)
    title = _clean_copy(data.get("article_title", ""), profile.title_max_chars)
    title_poster_copy = _clean_copy(data.get("title_poster_copy", ""), profile.title_max_chars)
    raw_posters = data.get("posters") if isinstance(data.get("posters"), list) else []

    posters: list[PosterText] = [
        PosterText(copy=title_poster_copy or title or "东意西形", scene="标题海报")
    ]
    for index, item in enumerate(raw_posters[: profile.poster_count], start=1):
        item = item if isinstance(item, dict) else {}
        copy = _clean_copy(item.get("copy", ""), profile.copy_max_chars)
        scene = _clean_copy(item.get("scene", ""), 80)
        if copy:
            posters.append(PosterText(copy=copy, scene=scene or f"意境海报 {index}"))

    # 模型偶尔少返回一两张时，宁可用已验证的中性短句补足，也不能改变文章结构。
    while len(posters) < required_count:
        index = len(posters)
        posters.append(PosterText(
            copy="让东方意蕴，在当代生活里安静展开。",
            scene=f"意境海报 {index}",
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
    safe_title = title.replace(forbidden_product_name, "").strip("，。；、 ") if forbidden_product_name else title
    return PosterPlan(
        article_title=safe_title or "东意西形",
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
    generate_image: Callable[[ImageGenerationRequest], Awaitable[object]],
) -> list[str]:
    """逐张图生图，确保所有海报共享同一 ERP 产品原图与完整品牌约束。"""

    if not reference_image_bytes:
        raise ValueError("纯海报任务缺少 ERP 产品参考图")
    urls: list[str] = []
    for index, poster in enumerate(plan.posters, start=1):
        prompt = _build_poster_prompt(
            profile=profile,
            product_name=product_name,
            poster=poster,
            position=index,
        )
        generated = await generate_image(ImageGenerationRequest(
            prompt=prompt,
            size="1024*1536",
            tenant_id=tenant_id,
            reference_image_bytes=reference_image_bytes,
            reference_content_type=reference_content_type or "image/jpeg",
        ))
        url = str(getattr(generated, "url", generated) or "").strip()
        if not url:
            raise RuntimeError(f"第 {index} 张海报未返回图片地址")
        urls.append(url)
    return urls


def _build_poster_prompt(
    *,
    profile: PublicationFormatProfile,
    product_name: str,
    poster: PosterText,
    position: int,
) -> str:
    """合成每张海报的不可省略视觉、主体和文字约束。"""

    return f"""这是第 {position} 张竖版长海报。以参考图中的 ERP 产品为唯一真实主体，
保持产品的材质、比例、结构和主体关系，不替换、不变形。产品名称仅供理解：{product_name}，
禁止把产品名称或型号写入画面。

画面场景：{poster.scene}
必须在画面上半部居中、清晰且完整地嵌入以下中文文案，逐字准确：
「{poster.copy}」

完整图片视觉规范（不可省略）：
{profile.visual_directives}

完整文案规范（不可省略）：
{profile.copy_directives}

硬性限制：只生成一张竖版长海报；不要二维码、不要品牌 logo、不要额外文字、
不要水印以外的联系方式，不要改变产品主体。"""


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
