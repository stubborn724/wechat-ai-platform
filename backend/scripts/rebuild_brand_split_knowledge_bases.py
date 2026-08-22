"""重建品牌定时发布所需的八个职责分离知识库。

每个品牌拆为“文章格式规则”和“产品背景说明”两份资料。格式库只服务文章或
海报文案 Agent，背景库只服务 ERP 产品图生图 Agent；任务同时绑定两库，由运行
时上下文服务按章节自动分流。脚本可重复执行，只替换同名的系统生成文档，不会
删除旧知识库、人工上传文档或投喂源配置。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.database import MysqlSessionLocal, PgSessionLocal
from app.models.mysql_models import ScheduledTask
from app.models.pg_models import KbDocument, KnowledgeBase
from app.services.knowledge_base_service import (
    create_knowledge_base,
    delete_document,
    process_document,
)
from app.services.footer_template_service import build_consultation_card_template


@dataclass(frozen=True)
class BrandSplitKnowledge:
    """一个 ERP 品牌的格式库、背景库与固定页脚定义。"""

    erp_source_key: str
    format_knowledge_base_name: str
    visual_knowledge_base_name: str
    footer_template: str
    format_document_text: str
    visual_document_text: str


# 咨询卡由固定页脚渲染器输出，二维码不再交由图片模型绘制。绣蔓额外保留抖音
# 入口，其他品牌先使用各自的企业微信入口；后续新增渠道只需追加一条二维码配置。
_XIUMAN_CONSULTATION_CARD = build_consultation_card_template(
    brand="绣蔓家具",
    phone="18682130473",
    qrcodes=(
        ("企业微信", "https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%94%AE%E5%89%8D%E9%94%80%E5%94%AE%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81%20%281%29.png"),
        ("抖音号 3746366286", "http://localhost:9002/wechat-assets/footer-assets/107/xiuman-douyin-qr.png"),
    ),
)
_ZHONGXIWUJIE_CONSULTATION_CARD = build_consultation_card_template(
    brand="中西无界",
    phone="18138381749",
    qrcodes=(("企业微信", "https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E4%B8%AD%E8%A5%BF%E6%97%A0%E7%95%8C%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png"),),
)
_XIEHUAI_CONSULTATION_CARD = build_consultation_card_template(
    brand="写怀",
    phone="18928694592",
    qrcodes=(("企业微信", "https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%86%99%E6%80%80%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png"),),
)
_JIANZHI_CONSULTATION_CARD = build_consultation_card_template(
    brand="剪纸系列",
    phone="18924894639",
    qrcodes=(("企业微信", "https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%89%AA%E7%BA%B8%E7%B3%BB%E5%88%97%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png"),),
)


BRAND_SPLIT_KNOWLEDGE = (
    BrandSplitKnowledge(
        erp_source_key="xiuman",
        format_knowledge_base_name="绣蔓家具文章格式规则",
        visual_knowledge_base_name="绣蔓家具背景说明",
        footer_template=_XIUMAN_CONSULTATION_CARD,
        format_document_text=f"""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。顺序固定为：标题海报图→3张竖版长图意境海报→末尾联系方式海报。所有文案嵌入图片内，文章正文区域不输出独立文字。

【文案要求】标题简短、有记忆点；每张长图内嵌文案不超过60字。围绕选中的具体产品，从材质、比例、空间关系或日常体验展开。表达温暖、克制，避免大众化套话、促销语言、价格和夸张承诺。

【标题要求】公众号草稿标题自然包含产品名称；海报画面标题保持简洁，不堆砌卖点。

【末尾联系方式】文章最后固定显示“产品咨询”咨询卡，展示品牌、咨询电话和企业微信/抖音二维码，不在正文或海报中重复联系方式。
【咨询卡】{_XIUMAN_CONSULTATION_CARD}""",
        visual_document_text="""【品牌调性】绣蔓家具以温暖、克制的现代家居审美表达日常生活，强调材质、比例与空间关系的自然平衡。

【背景要求】围绕 ERP 原图中的产品构建高级、自然且可居住的现代家居场景。保留产品结构、材质、比例与颜色，不替换产品主体，只改变空间背景和陈设关系。

【图片要求】竖版产品场景图，暖色调、柔和自然的室内氛围，产品主体清晰。每篇图片由程序仅在参考图已知视角内轮换：正视、轻微斜视、轻微俯视、已见材质细节和原视角空间广角；没有明确可见的背面、侧后、底部或内部结构时不得幻觉补造。模型不得生成任何可读文字、数字、电话号码、二维码、品牌 logo 或水印；联系方式只由程序在文章底部咨询卡中渲染。禁止额外产品和无关文字。""",
    ),
    BrandSplitKnowledge(
        erp_source_key="zhongxiwujie",
        format_knowledge_base_name="中西无界文章格式规则",
        visual_knowledge_base_name="中西无界背景说明",
        footer_template=_ZHONGXIWUJIE_CONSULTATION_CARD,
        format_document_text=f"""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。顺序固定为：标题海报图→3张竖版长图意境海报→末尾联系方式海报。所有文案嵌入图片内，正文区域不输出独立文字，画面不显示产品型号。

【文案要求】文案体现东情西韵的碰撞感，兼具东方人文底蕴与西方奢品质感；用词高级沉稳、克制有格调，不使用推销和广告口吻。每张长图约50字，围绕具体产品诠释中西融合的设计美学、工艺价值与高端生活方式。

【标题要求】海报主标题不超过12字，具有文化厚重感；公众号草稿标题自然包含产品名称。

【末尾联系方式】文章最后固定显示“产品咨询”咨询卡，展示品牌、咨询电话和企业微信二维码，不在正文或海报中重复联系方式。
【咨询卡】{_ZHONGXIWUJIE_CONSULTATION_CARD}""",
        visual_document_text="""【品牌调性】中西无界以“东意西形”为核心美学，取明清中式家具神韵与榫卯工艺，融合当代欧洲奢品设计语言，呈现贵气内敛、沉稳高级、文化厚重的东方都会奢雅气质。

【背景要求】ERP 产品为唯一真实主体，必须保留其材质、比例、结构与关键设计特征。背景呈现低饱和中西无界高端家居场景，空间与陈设柔和克制，形成东方与当代欧洲美学自然相融的层次。

【图片要求】竖版长海报比例，墨绿、正红、阔叶黄檀木色、古铜金高级撞色。隐约呈现红木质感、铜件光泽、天然大理石纹理、榫卯线条与意式极简轮廓。画面上半部分预留优雅复古衬线文字区域；左下角添加艺术字水印“中西无界TEL:18138381749”。禁止二维码、品牌 logo、额外文字和改变产品主体。""",
    ),
    BrandSplitKnowledge(
        erp_source_key="xiehuai",
        format_knowledge_base_name="写怀文章格式规则",
        visual_knowledge_base_name="写怀背景说明",
        footer_template=_XIEHUAI_CONSULTATION_CARD,
        format_document_text=f"""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。顺序固定为：标题海报图→3张竖版长图意境海报→末尾联系方式海报。所有文案嵌入图片内，正文区域不输出独立文字。

【文案要求】文案具有东方文人意境，诗意悠远，传递禅意与雅趣生活。每张长图内嵌文案控制在30至90字，围绕产品线条、比例、材质与生活意境表达；不写价格、促销、参数和夸张承诺。

【标题要求】海报主标题不超过12字，隽永且有留白感；公众号草稿标题自然包含产品名称。

【末尾联系方式】文章最后固定显示“产品咨询”咨询卡，展示品牌、咨询电话和企业微信二维码，不在正文或海报中重复联系方式。
【咨询卡】{_XIEHUAI_CONSULTATION_CARD}""",
        visual_document_text="""【品牌调性】写怀融入明宋文化、宋式极简和当代西方设计，追求东方神韵与西方优雅的平衡。整体气质为国际新东方、禅意悠远、极简高级、意境留白、温润素雅。

【背景要求】ERP 产品为唯一主体，保持其轮廓、结构、材质和比例。以低饱和新东方家居实景作为背景，空间具有留白、宁静与可居住感，不堆叠装饰，不生成与产品无关的家具主体。

【图片要求】竖版长海报，空间半透明柔和、光影克制；主色为浅灰、淡雅素色与浅原木。画面上半部预留优雅复古衬线文字区域；右下角添加艺术字水印“写怀 TEL: 18928694592”。禁止二维码、品牌 logo、额外文字和改变产品主体。""",
    ),
    BrandSplitKnowledge(
        erp_source_key="jianzhi",
        format_knowledge_base_name="剪纸系列文章格式规则",
        visual_knowledge_base_name="剪纸系列背景说明",
        footer_template=_JIANZHI_CONSULTATION_CARD,
        format_document_text=f"""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。顺序固定为：标题海报图→3张竖版长图意境海报→末尾联系方式海报。所有文案嵌入图片内，正文区域不输出独立文字。

【文案要求】内嵌文案诗意、有画面感和意境留白，如生活美学短句。每张长图控制在30至60字，围绕具体产品表达设计感与生活美感；不写参数、价格、促销和直接购买引导。

【标题要求】海报主标题不超过15字，简洁且具当代艺术感；公众号草稿标题自然包含产品名称。

【末尾联系方式】文章最后固定显示“产品咨询”咨询卡，展示品牌、咨询电话和企业微信二维码，不在正文或海报中重复联系方式。
【咨询卡】{_JIANZHI_CONSULTATION_CARD}""",
        visual_document_text="""【品牌调性】剪纸系列以“在西的潮流中，我们向东”为核心理念，融合东方意境与西方高级设计。剪纸镂空纹样、珍珠与玫瑰花意象形成透空艺术感，整体高级、当代艺术、温暖雅致且艳而不俗。

【背景要求】ERP 产品是画面唯一真实主体，保持原始结构、材质、比例与颜色。背景使用高级家居场景与温暖艺术氛围，融入剪纸艺术的通透感、几何镂空光影和节制的东方意象，不喧宾夺主。

【图片要求】竖版长海报，温暖雅致但不过度艳丽，产品轮廓清晰。画面上半部预留精致、有呼吸感的文字区域；右下角添加艺术字水印“剪纸系列 TEL：18924894639”。禁止二维码、品牌 logo、额外文字和改变产品主体。""",
    ),
)


def _get_or_create_knowledge_base(
    pg_db,
    *,
    tenant_id: int,
    name: str,
    kb_type: str,
    description: str,
) -> KnowledgeBase:
    """按租户和名称复用目标知识库，避免每次执行产生同名副本。"""

    knowledge_base = (
        pg_db.query(KnowledgeBase)
        .filter(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.name == name)
        .first()
    )
    if knowledge_base:
        # 历史上可能有人误停用了同名系统库；本脚本明确重建该库，因此恢复为可用。
        knowledge_base.is_active = 1
        pg_db.commit()
        return knowledge_base
    return create_knowledge_base(
        pg_db,
        tenant_id=tenant_id,
        name=name,
        kb_type=kb_type,
        description=description,
    )


def _replace_generated_document(
    pg_db,
    *,
    knowledge_base: KnowledgeBase,
    tenant_id: int,
    filename: str,
    content: str,
) -> None:
    """只替换系统生成的同名文档，保留用户可能手工上传的其他资料。"""

    old_documents = (
        pg_db.query(KbDocument)
        .filter(
            KbDocument.knowledge_base_id == knowledge_base.id,
            KbDocument.filename == filename,
        )
        .all()
    )
    for document in old_documents:
        delete_document(pg_db, document.id, tenant_id=tenant_id)

    document = process_document(
        pg_db,
        knowledge_base.id,
        tenant_id,
        content.encode("utf-8"),
        filename,
    )
    if document.status != "ready":
        raise RuntimeError(
            f"知识库“{knowledge_base.name}”文档处理失败：{document.error_message or document.status}"
        )


def rebuild_brand_split_knowledge_bases() -> None:
    """创建八库并把 ERP 定时任务绑定到同品牌的格式库与背景库。

    任务通过 ``erp_image_config.source_key`` 匹配，而非固定任务 ID。这样编辑、
    重建或新增同一品牌任务后，脚本仍能稳定为其绑定正确的两份规则资料。
    """

    profiles_by_source = {item.erp_source_key: item for item in BRAND_SPLIT_KNOWLEDGE}
    prepared_pairs: dict[tuple[int, str], tuple[KnowledgeBase, KnowledgeBase]] = {}
    mysql_db = MysqlSessionLocal()
    pg_db = PgSessionLocal()
    try:
        tasks = mysql_db.query(ScheduledTask).filter(
            ScheduledTask.erp_image_config.isnot(None)
        ).all()
        matched_task_count = 0
        for task in tasks:
            source_key = str((task.erp_image_config or {}).get("source_key") or "").strip()
            profile = profiles_by_source.get(source_key)
            if profile is None:
                continue

            cache_key = (task.tenant_id, source_key)
            if cache_key not in prepared_pairs:
                format_kb = _get_or_create_knowledge_base(
                    pg_db,
                    tenant_id=task.tenant_id,
                    name=profile.format_knowledge_base_name,
                    kb_type="publication_format",
                    description="仅包含文章结构、文案限制和固定联系方式的定时发布规范。",
                )
                visual_kb = _get_or_create_knowledge_base(
                    pg_db,
                    tenant_id=task.tenant_id,
                    name=profile.visual_knowledge_base_name,
                    kb_type="brand_visual",
                    description="仅包含 ERP 产品图生图所需的品牌调性、场景与背景规则。",
                )
                _replace_generated_document(
                    pg_db,
                    knowledge_base=format_kb,
                    tenant_id=task.tenant_id,
                    filename="系统生成：文章格式规则.txt",
                    content=profile.format_document_text,
                )
                _replace_generated_document(
                    pg_db,
                    knowledge_base=visual_kb,
                    tenant_id=task.tenant_id,
                    filename="系统生成：背景说明.txt",
                    content=profile.visual_document_text,
                )
                prepared_pairs[cache_key] = (format_kb, visual_kb)

            format_kb, visual_kb = prepared_pairs[cache_key]
            # 同时绑定两份库：运行时会按章节把格式库送往文章 Agent，把背景库送往
            # 图片 Agent。保留 feed_source 字段，确保投喂源仍可以决定文章结构。
            task.knowledge_base_ids = [format_kb.id, visual_kb.id]
            task.footer_template = profile.footer_template
            matched_task_count += 1

        if matched_task_count == 0:
            raise RuntimeError("未找到已配置四个品牌 ERP 来源的定时任务，未创建知识库")
        mysql_db.commit()
        print(f"已重建 {len(prepared_pairs) * 2} 个知识库，并更新 {matched_task_count} 个定时任务")
    except Exception:
        mysql_db.rollback()
        raise
    finally:
        pg_db.close()
        mysql_db.close()


if __name__ == "__main__":
    rebuild_brand_split_knowledge_bases()
