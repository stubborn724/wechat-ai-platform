"""投喂源联系方式过滤服务。

投喂源文章末尾的购买提示、电话、二维码和联系卡只属于来源账号的运营信息，不能
参与正文仿写。该模块统一供 HTML 蓝图、Markdown 上下文和结构模板使用，避免不同
生成入口因为识别规则不一致再次将参考联系方式带入新文章。
"""

from __future__ import annotations

from app.schemas.article import LayoutSection, LayoutTemplate


# 强联系词可直接识别为运营联系区；购买动作词必须同时命中多个，避免把正常正文中
# 的单个“咨询”或“详情”误判为联系卡。
CONTACT_TEXT_KEYWORDS = (
    "联系我们", "contact us", "客服", "联系电话", "电话", "tel", "企业微信",
    "二维码", "扫码", "关注我们", "官方渠道", "官网", "小程序", "预约咨询", "购买咨询", "报价咨询",
    "温馨提示", "购买须知", "试坐优先",
)
CONTACT_ACTION_KEYWORDS = ("试坐", "如需购买", "产品详情", "购买", "咨询", "报价", "预约")
CONTACT_IMAGE_KEYWORDS = ("qrcode", "qr-code", "二维码", "企业微信", "wechat", "weixin")
CONTACT_SECTION_ROLE_KEYWORDS = (
    "contact", "footer", "qrcode", "qr", "wechat", "weixin", "cta", "call_to_action",
    "联系", "页脚", "二维码",
)


def is_reference_contact_text(value: str) -> bool:
    """判断文本是否属于参考文章末尾的联系或购买引导内容。"""

    normalized = " ".join(str(value or "").lower().split())
    if not normalized:
        return False
    if any(keyword in normalized for keyword in CONTACT_TEXT_KEYWORDS):
        return True
    return sum(keyword in normalized for keyword in CONTACT_ACTION_KEYWORDS) >= 2


def is_reference_contact_image_identity(value: str) -> bool:
    """根据图片 URL、alt 等稳定标识识别二维码和企业微信联系图。"""

    normalized = " ".join(str(value or "").lower().split())
    return bool(normalized) and any(
        keyword in normalized for keyword in CONTACT_IMAGE_KEYWORDS
    )


def strip_reference_contact_markdown(markdown: str) -> str:
    """删除 Markdown 中从末尾联系区开始的全部内容。

    投喂源的联系区通常位于文章末尾。遇到“联系我们”、购买引导或二维码行即截断，
    比只删除图片更可靠，因为剩余电话、提示语仍会作为 Agent 的仿写素材。
    """

    lines = str(markdown or "").splitlines()
    for index, line in enumerate(lines):
        if is_reference_contact_text(line) or is_reference_contact_image_identity(line):
            return "\n".join(lines[:index]).strip()
    return "\n".join(lines).strip()


def remove_contact_sections_from_layout_template(template: LayoutTemplate) -> LayoutTemplate:
    """移除结构模板中的联系章节，并重新计算剩余正文的结构统计。

    结构模板本身不保存原文电话，但 ``contact_section`` 仍会强迫 Agent 生成一个新的
    联系卡。删除整个章节而不是清空 block，才能确保正文、图片槽位和版式均不保留
    参考联系方式的形态；最终只由任务固定页脚统一追加联系信息。
    """

    retained_sections = [
        section for section in template.sections
        if not _is_reference_contact_section(section)
    ]
    total_paragraph_count = sum(
        block.count
        for section in retained_sections
        for block in section.blocks
        if block.type in {"paragraph", "quote", "note", "list", "image_caption"}
    )
    total_image_count = sum(
        block.count
        for section in retained_sections
        for block in section.blocks
        if block.type == "image"
    )
    layout_features = [
        feature
        for feature in template.layout_features
        if not is_reference_contact_text(feature)
        and not _contains_contact_role_keyword(feature)
    ]
    return LayoutTemplate(
        schema_version=template.schema_version,
        sections=retained_sections,
        ending_style=template.ending_style,
        total_paragraph_count=total_paragraph_count,
        total_image_count=total_image_count,
        layout_features=layout_features,
    )


def _is_reference_contact_section(section: LayoutSection) -> bool:
    """识别用于联系卡的结构章节，不依赖某个固定的历史 role 命名。"""

    if _contains_contact_role_keyword(section.section_role):
        return True
    return any(
        _contains_contact_role_keyword(block.role or "")
        or _contains_contact_role_keyword(block.style_pattern or "")
        for block in section.blocks
    )


def _contains_contact_role_keyword(value: str) -> bool:
    """判断结构元数据是否表达联系、页脚或二维码语义。"""

    normalized = str(value or "").lower()
    return any(keyword in normalized for keyword in CONTACT_SECTION_ROLE_KEYWORDS)
