"""投喂文章格式模板的程序化分析服务。

本模块负责把一次性的投喂文章结构转换成可长期复用的格式模板。它不调用大模型：
DOM 槽位、图片数量和连续海报判定都是确定性的，因此应由程序掌握，避免每次定时
任务都将原 HTML 与样式重复发送给模型，既减少 token，也避免模型误改版式。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.services.html_imitation_service import (
    HtmlImageSlot,
    HtmlImitationBlueprint,
    HtmlTextSlot,
    analyze_html_for_imitation,
)
from app.services.publication_format_service import PublicationFormatProfile


@dataclass(frozen=True)
class AnalyzedFormatProfile:
    """尚未写入数据库的格式模板分析结果。

    ``template_payload`` 仅保存程序渲染所需的结构化数据；模型得到的内容提示仍由
    ``HtmlImitationBlueprint.prompt_payload`` 动态压缩生成，防止完整 HTML 进入提示词。
    """

    article_id: int
    name: str
    render_mode: str
    template_payload: dict[str, Any]
    title_policy: dict[str, Any]


def analyze_feed_article_format(
    *,
    article_id: int,
    article_title: str,
    body_html: str,
) -> AnalyzedFormatProfile:
    """分析一篇投喂文章并生成可复用的格式模板。

    当文章包含可生成文本时，优先使用 ``html_slots`` 保留原 DOM；纯图片文章则
    归为 ``poster_gallery``，由无缝海报渲染器输出。两种模板都只保存结构规则，
    不保留原文作为下次模型生成的输入。
    """

    normalized_html = str(body_html or "").strip()
    if not normalized_html:
        raise ValueError("格式模板需要投喂文章的 HTML 内容")

    try:
        blueprint = analyze_html_for_imitation(normalized_html)
    except ValueError as exc:
        # 纯图片海报没有文字槽位，HTML 仿写蓝图会按设计拒绝。此处不把异常泄漏
        # 给调用方，而是将其转换为连续海报模板，避免为海报专门增加 Agent。
        if "未找到可生成的文字" not in str(exc):
            raise
        return _build_poster_profile(article_id, article_title, normalized_html)

    return AnalyzedFormatProfile(
        article_id=article_id,
        name=_build_profile_name(article_title, "HTML 版式"),
        render_mode="html_slots",
        template_payload={"blueprint": _serialize_html_blueprint(blueprint)},
        title_policy={
            # 只有真实标题标签才允许接收首屏视觉标题。首段 p 是导语，若把它误当
            # 标题会覆盖正文，破坏普通图文的文章节奏。
            "visual_title_slot_id": next(
                (
                    slot.slot_id
                    for slot in blueprint.text_slots
                    if slot.tag_name in {"h1", "h2", "h3", "h4", "h5", "h6"}
                ),
                None,
            ),
            "visual_subtitle_slot_id": None,
            "wechat_title_source": "generated",
        },
    )


def html_blueprint_from_profile_payload(
    template_payload: Mapping[str, Any],
) -> HtmlImitationBlueprint:
    """把数据库 JSON 恢复为 HTML 槽位蓝图。

    恢复函数是唯一的反序列化入口，任务执行器不需要理解 JSON 内部形状；版本升级
    时可在这里集中兼容旧模板，而不影响已上线的正式 ERP 任务。
    """

    raw_blueprint = template_payload.get("blueprint") if template_payload else None
    if not isinstance(raw_blueprint, Mapping):
        raise ValueError("HTML 格式模板缺少 blueprint")
    return HtmlImitationBlueprint(
        html_template=str(raw_blueprint.get("html_template") or ""),
        text_slots=tuple(
            HtmlTextSlot(
                slot_id=str(item["slot_id"]),
                tag_name=str(item["tag_name"]),
                original_text=str(item["original_text"]),
                target_length=int(item["target_length"]),
            )
            for item in raw_blueprint.get("text_slots", [])
        ),
        image_slots=tuple(
            HtmlImageSlot(
                slot_id=str(item["slot_id"]),
                source_url=str(item["source_url"]),
                original_alt=str(item["original_alt"]),
                position=int(item["position"]),
            )
            for item in raw_blueprint.get("image_slots", [])
        ),
    )


def apply_poster_template_to_publication_profile(
    publication_profile: PublicationFormatProfile,
    template_payload: Mapping[str, Any],
) -> PublicationFormatProfile:
    """以投喂模板覆盖海报切片数量，保留知识库的品牌视觉与页脚规则。

    投喂文章决定“连续几张图”的输出形式，知识库仍是品牌色彩、场景、文案限制和
    联系方式的唯一来源。两类规则分层组合，避免为了通用化而丢失现有无缝海报效果。
    """

    requested_count = int(template_payload.get("poster_count") or 0)
    if requested_count < 1 or requested_count > 30:
        raise ValueError("海报格式模板的图片数量必须在 1 到 30 之间")
    return PublicationFormatProfile(
        is_poster_gallery=True,
        poster_count=requested_count,
        title_max_chars=publication_profile.title_max_chars,
        copy_max_chars=publication_profile.copy_max_chars,
        raw_directives=publication_profile.raw_directives,
        image_directives=publication_profile.image_directives,
        visual_directives=publication_profile.visual_directives,
        copy_directives=publication_profile.copy_directives,
        footer_template=publication_profile.footer_template,
    )


def _build_poster_profile(
    article_id: int,
    article_title: str,
    body_html: str,
) -> AnalyzedFormatProfile:
    """从纯图片 HTML 提取连续海报模板，固定由零间距渲染器输出。"""

    from bs4 import BeautifulSoup

    image_count = len(BeautifulSoup(body_html, "html.parser").find_all("img"))
    if image_count < 1:
        raise ValueError("投喂文章既没有可生成文字，也没有图片海报")
    return AnalyzedFormatProfile(
        article_id=article_id,
        name=_build_profile_name(article_title, "无缝海报"),
        render_mode="poster_gallery",
        template_payload={
            # 当前文章图片数量是初始模板建议，不是硬编码上限；任务可在后续编辑
            # 时按成本目标调整，渲染器仍以实际生成结果为准。
            "poster_count": image_count,
            "seamless": True,
        },
        title_policy={
            "visual_title_mode": "first_poster",
            "wechat_title_source": "generated",
        },
    )


def _serialize_html_blueprint(blueprint: HtmlImitationBlueprint) -> dict[str, Any]:
    """把不可变蓝图转为 JSON 可持久化的数据。"""

    return {
        "html_template": blueprint.html_template,
        "text_slots": [asdict(slot) for slot in blueprint.text_slots],
        "image_slots": [asdict(slot) for slot in blueprint.image_slots],
    }


def _build_profile_name(article_title: str, suffix: str) -> str:
    """生成稳定可识别的模板名称，名称不参与模型提示词。"""

    title = str(article_title or "未命名投喂文章").strip()
    return f"{title[:80]} {suffix}".strip()
