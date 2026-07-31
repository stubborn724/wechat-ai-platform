"""创建四个品牌的完整海报发布知识库，并切换现有定时任务。

旧知识库保留不删除，避免历史检索、文章或人工资料丢失。新知识库将品牌调性、
图片内文案规则和末尾联系方式放在同一份无凭证文档中；定时任务只绑定新库，
由发布格式服务从全文提取不可截断的强约束。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.database import MysqlSessionLocal, PgSessionLocal
from app.models.mysql_models import ScheduledTask
from app.models.pg_models import KbDocument, KnowledgeBase
from app.services.knowledge_base_service import create_knowledge_base, process_document


@dataclass(frozen=True)
class BrandPosterKnowledge:
    """一个品牌的 ERP 来源、发布页脚与可直接入库的规范文档。"""

    task_id: int
    knowledge_base_name: str
    footer_template: str
    document_text: str


_BRAND_POSTER_KNOWLEDGE = (
    BrandPosterKnowledge(
        task_id=1,
        knowledge_base_name="绣蔓家具海报发布规范",
        footer_template=(
            "绣蔓家具TEL:18682130473\n"
            "![二维码](https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%94%AE%E5%89%8D%E9%94%80%E5%94%AE%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81%20%281%29.png)"
        ),
        document_text="""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。结构为：标题海报图→3张竖版长图意境海报→末尾联系方式海报。所有文案全部嵌入图片内，文章正文区域不放任何独立文字。
【品牌调性】绣蔓家具以温暖、克制的现代家居审美表达日常生活，强调材质、比例与空间关系的自然平衡。
【文案要求】标题简短有记忆点；每张长图内嵌文案不超过60字，避免大众化、避免重复，不使用推销式广告语言。
【图片要求】竖版长海报比例，暖色调、高级自然家居场景，产品主体清晰，文字在上半部分居中且完整可读。每张图右下角添加艺术字水印“绣蔓家具TEL:18682130473”。禁止内嵌二维码，禁止设计品牌logo。
【末尾联系方式】文章最后固定显示联系方式文案“绣蔓家具TEL:18682130473”，并附上企业微信二维码图片：https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%94%AE%E5%89%8D%E9%94%80%E5%94%AE%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81%20%281%29.png，不额外补充任何文案和不修改图片。""",
    ),
    BrandPosterKnowledge(
        task_id=2,
        knowledge_base_name="中西无界海报发布规范",
        footer_template=(
            "中西无界TEL: 18138381749\n"
            "![二维码](https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E4%B8%AD%E8%A5%BF%E6%97%A0%E7%95%8C%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png)"
        ),
        document_text="""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。结构为：标题海报图→2~3张竖版长图意境海报→末尾联系方式海报。所有文案全部嵌入图片内，文章正文区域不放任何独立文字，不显示产品型号。
【品牌调性】中西无界是当代高雅原创家具品牌，创立于2016年，以民国文化为设计背景，融贯东西。以“东意西形”为核心美学，取明清中式家具神韵与榫卯工艺，融合当代欧洲奢品设计语言，重新诠释空间中人与物、传统与现代、东方与西方的关系。全系用材考究：酸枝红木、天然奢石、头层青皮、黄铜金属件，呈现贵气内敛、沉稳高级、文化厚重的东方都会奢雅气质。
【文案要求】图片内嵌文案体现东情西韵的碰撞感，既有东方人文底蕴又有西方奢品质感，用词高级沉稳、克制有格调。每张长图内嵌文案控制在50字左右，围绕选中的具体产品展开，诠释中西融合的设计美学、工艺价值与高端生活方式。主标题不超过12字，有文化厚重感。
【图片要求】竖版长海报比例，画面朦胧柔和，整体氛围感强。低饱和度中西无界高端家居场景半透明虚化，产品与空间柔和，突出文字。采用墨绿、正红、阔叶黄檀木色、古铜金高级撞色；上半部分居中使用优雅复古衬线字体。隐约呈现红木、铜件、天然大理石、中式榫卯线条与意式极简轮廓。左下角添加艺术字水印“中西无界TEL:18138381749”。禁止内嵌二维码，以及不能设计品牌logo。
【末尾联系方式】文章最后固定显示联系方式文案“中西无界TEL: 18138381749”，并附上企业微信二维码图片：https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E4%B8%AD%E8%A5%BF%E6%97%A0%E7%95%8C%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png，不额外补充任何文案和不修改图片。""",
    ),
    BrandPosterKnowledge(
        task_id=3,
        knowledge_base_name="写怀海报发布规范",
        footer_template=(
            "写怀TEL: 18928694592\n"
            "![二维码](https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%86%99%E6%80%80%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png)"
        ),
        document_text="""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。结构为：标题海报图→2~3张竖版长图意境海报→末尾联系方式海报。所有文案全部嵌入图片内，文章正文区域不放任何独立文字。
【品牌调性】写怀以“东意西形”为核心，融入明宋文化、宋式极简和当代西方设计，追求东方神韵与西方优雅的平衡。整体气质为国际新东方、禅意悠远、极简高级、意境留白、温润素雅。
【文案要求】文案具有东方文人意境，诗意悠远，体现禅意与雅趣生活；每张长图内嵌文案控制在30~90字，围绕产品线条、比例与生活意境表达。主标题不超过12字，隽永且有留白感。
【图片要求】竖版长图海报，低饱和度写怀新东方家居实景，空间半透明虚化、光影柔和；主色为浅灰、淡雅素色与浅原木。文字在画面上半部分居中，采用优雅复古衬线字体。每张图右下角添加艺术字水印“写怀 TEL: 18928694592”。禁止内嵌二维码，以及不能设计品牌logo。
【末尾联系方式】文章最后固定显示联系方式文案“写怀TEL: 18928694592”，并附上企业微信二维码图片：https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%86%99%E6%80%80%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png，不额外补充任何文案和不修改图片。""",
    ),
    BrandPosterKnowledge(
        task_id=4,
        knowledge_base_name="剪纸系列海报发布规范",
        footer_template=(
            "剪纸系列TEL：18924894639\n"
            "![二维码](https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%89%AA%E7%BA%B8%E7%B3%BB%E5%88%97%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png)"
        ),
        document_text="""【文章形式】纯海报拼接形式，无独立文字段落，整篇由图片构成。结构为：标题海报图→2~3张竖版长图意境海报→末尾联系方式海报。所有文案全部嵌入图片内，文章正文区域不放任何独立文字。
【品牌调性】梵奢剪纸3.0系列以“在西的潮流中，我们向东”为核心理念，融合东方意境与西方高级设计。剪纸镂空纹样、珍珠与玫瑰花意象形成透空艺术感，整体高级、当代艺术、温暖雅致且艳而不俗。
【文案要求】内嵌文案诗意、有画面感和意境留白，像生活美学短句；每张长图控制在30~60字，围绕具体产品表达设计感与生活美感。主标题不超过15字。
【图片要求】竖版长图海报，高级感家居场景与温暖艺术光影；产品是画面主体，融入剪纸艺术通透感和几何镂空光影。文字排版精致有呼吸感。每张图右下角添加艺术字水印“剪纸系列 TEL：18924894639”。禁止内嵌二维码，以及不能设计品牌logo。
【末尾联系方式】文章最后固定显示联系方式文案“剪纸系列TEL：18924894639”，并附上企业微信二维码图片：https://xiumancloud.oss-cn-beijing.aliyuncs.com/%E5%85%AC%E5%8F%B8%E8%81%94%E7%B3%BB%E6%96%B9%E5%BC%8F%E4%BA%8C%E7%BB%B4%E7%A0%81/%E5%89%AA%E7%BA%B8%E7%B3%BB%E5%88%97%E4%BC%81%E4%B8%9A%E5%BE%AE%E4%BF%A1%E4%BA%8C%E7%BB%B4%E7%A0%81.png，不额外补充任何文案和不修改图片。""",
    ),
)


def _get_or_create_profile_knowledge_base(db, tenant_id: int, name: str) -> KnowledgeBase:
    """复用同名新知识库，保证脚本重复运行不会创建重复资料。"""

    existing = db.query(KnowledgeBase).filter(
        KnowledgeBase.tenant_id == tenant_id,
        KnowledgeBase.name == name,
    ).first()
    if existing:
        return existing
    return create_knowledge_base(
        db,
        tenant_id=tenant_id,
        name=name,
        kb_type="publication_profile",
        description="完整海报发布规范：不参与普通截断召回，供定时发布强约束使用。",
    )


def rebuild_brand_poster_knowledge_bases() -> None:
    """创建规范知识库、导入清洗文档并原子切换四个现有定时任务。"""

    mysql_db = MysqlSessionLocal()
    pg_db = PgSessionLocal()
    try:
        for item in _BRAND_POSTER_KNOWLEDGE:
            task = mysql_db.query(ScheduledTask).filter(ScheduledTask.id == item.task_id).first()
            if not task:
                raise RuntimeError(f"找不到待迁移的定时任务 #{item.task_id}")
            knowledge_base = _get_or_create_profile_knowledge_base(
                pg_db, task.tenant_id, item.knowledge_base_name,
            )
            has_document = pg_db.query(KbDocument).filter(
                KbDocument.knowledge_base_id == knowledge_base.id,
            ).first()
            if not has_document:
                process_document(
                    pg_db,
                    knowledge_base.id,
                    task.tenant_id,
                    item.document_text.encode("utf-8"),
                    "完整海报发布规范.txt",
                )

            # 发布格式仅由新知识库控制；投喂源引用必须清空，防止旧 HTML 模板
            # 再次把段落文字或原二维码带回纯海报文章。
            task.writing_mode = "kb"
            task.feed_source_ids = None
            task.feed_source_id = None
            task.feed_article_ids = None
            task.knowledge_base_ids = [knowledge_base.id]
            task.footer_template = item.footer_template
            # 名称也是任务配置的一部分。切换后必须移除“投喂源仿写”字样，
            # 以免运营人员误以为任务还依赖外部文章模板。
            task.name = item.knowledge_base_name.replace("发布规范", "定时海报")
        mysql_db.commit()
    except Exception:
        mysql_db.rollback()
        raise
    finally:
        pg_db.close()
        mysql_db.close()


if __name__ == "__main__":
    rebuild_brand_poster_knowledge_bases()
    print("四个品牌的海报发布知识库与定时任务已切换完成")
