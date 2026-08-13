"""她格原创图文的专用 HTML 模板与任务绑定配置。

她格的内容是知识库驱动的企业服务图文，不能复用家具海报或自由 Markdown 的排版。
本模块把视觉结构固化为可持久化的投喂文章：文字由 Agent 填充，图片只能替换预先
定义的 DOM 槽位。这样正文图片永远位于相应段落之间，不再依赖模型输出标题格式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.mysql_models import FeedSource, FeedSourceArticle
from app.services.format_profile_persistence_service import create_or_reuse_format_profile


SHEGE_TEMPLATE_SOURCE_NAME = "她格原创图文版式"
SHEGE_TEMPLATE_SOURCE_SLUG = "shege-original-html-template"
SHEGE_TEMPLATE_SOURCE_IDENTIFIER = "system:shege-original-html-template-v1"
SHEGE_TEMPLATE_ARTICLE_TITLE = "她格原创图文结构模板"


@dataclass(frozen=True)
class ShegeHtmlTemplateBinding:
    """投喂源、模板文章和格式模板的稳定关联结果。"""

    feed_source_id: int
    feed_article_id: int
    format_profile_id: int


def build_shege_html_template() -> str:
    """构造她格公众号的结构化图文 HTML。

    四个 ``img`` 节点分别承担封面、问题洞察、落地路径和验收复盘。它们在投喂
    文章中就已固定在各章节之间，后续 HTML 渲染器只会替换 ``src``，不会追加到
    文末。内联样式采用微信编辑器兼容的基础 CSS，避免依赖外部样式表。
    """

    return """
<section data-shege-layout="enterprise-ai-v1" style="max-width:677px;margin:0 auto;padding:0 16px;background:#ffffff;color:#263238;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif;line-height:1.85;">
  <div style="height:4px;width:56px;background:#0f766e;margin:28px 0 18px;"></div>
  <p style="margin:0 0 12px;color:#0f766e;font-size:13px;font-weight:600;">她格 / AI 入企观察</p>
  <h1 style="margin:0 0 18px;color:#183c3a;font-size:25px;line-height:1.45;font-weight:700;">从一个经营问题开始，让 AI 真正进入企业日常</h1>
  <p style="margin:0 0 22px;padding:16px 18px;border-left:3px solid #6baaa2;background:#f3f8f7;color:#46605e;font-size:15px;">企业应用 AI 的关键，不是增加一个工具，而是让一个真实业务环节更清晰、更可执行、更能被复盘。</p>
  <figure style="margin:0 0 30px;"><img src="" alt="经营问题与管理决策场景" style="display:block;width:100%;height:auto;border-radius:4px;" /></figure>
  <section style="margin:0 0 30px;">
    <h2 style="margin:0 0 14px;color:#183c3a;font-size:19px;line-height:1.55;font-weight:700;">先把经营问题说清楚</h2>
    <p style="margin:0;color:#3f4e4d;font-size:16px;">很多企业的焦虑并非来自技术不足，而是问题没有被拆成可以验证的业务动作。先看清成本、效率、客户或协同环节中最需要改善的地方，AI 才有明确的落点。</p>
  </section>
  <figure style="margin:0 0 30px;"><img src="" alt="业务流程与团队协同场景" style="display:block;width:100%;height:auto;border-radius:4px;" /></figure>
  <section style="margin:0 0 30px;padding:20px 18px;background:#f7faf9;border-radius:4px;">
    <h2 style="margin:0 0 14px;color:#183c3a;font-size:19px;line-height:1.55;font-weight:700;">把 AI 变成可执行的路径</h2>
    <p style="margin:0;color:#3f4e4d;font-size:16px;">从数据准备、场景试点到流程协同，每一步都应有负责人与判断标准。把目标放进日常流程，而不是停留在演示或概念阶段，才能形成持续的经营改善。</p>
  </section>
  <figure style="margin:0 0 30px;"><img src="" alt="数据复盘与经营决策场景" style="display:block;width:100%;height:auto;border-radius:4px;" /></figure>
  <section style="margin:0 0 30px;">
    <h2 style="margin:0 0 14px;color:#183c3a;font-size:19px;line-height:1.55;font-weight:700;">用结果决定下一步</h2>
    <p style="margin:0;color:#3f4e4d;font-size:16px;">以业务指标、使用频率和协作成本为依据持续复盘。有效的做法被沉淀为组织能力，不适合的方案及时回退，企业才能在可控节奏中完成真正的 AI 落地。</p>
  </section>
  <figure style="margin:0 0 32px;"><img src="" alt="企业管理复盘与持续优化场景" style="display:block;width:100%;height:auto;border-radius:4px;" /></figure>
</section>
""".strip()


def build_shege_template_markdown() -> str:
    """提供投喂源列表展示用的纯文本摘要，不携带参考图片 URL。"""

    return (
        "她格原创图文结构模板\n\n"
        "封面导语、经营问题、落地路径、复盘验收四段式图文结构。"
    )


def build_shege_template_task_patch(
    *,
    feed_source_id: int,
    feed_article_id: int,
    format_profile_id: int,
) -> dict[str, Any]:
    """构造她格任务的显式模板绑定字段。

    模板 ID 固定写入任务而不是依赖自动发现，避免用户后续在同一投喂源导入其他
    文章后，生产任务悄悄切换版式。图片数固定为四张，兼顾段间阅读节奏和生成成本。
    """

    return {
        "writing_mode": "feed",
        "feed_source_id": int(feed_source_id),
        "feed_source_ids": [int(feed_source_id)],
        "feed_article_ids": [int(feed_article_id)],
        "format_profile_id": int(format_profile_id),
        "format_profile_auto_bind_enabled": False,
        "template_rotation_config": None,
        "template_rotation_version": 0,
        "html_image_count": 4,
    }


def ensure_shege_html_template_binding(
    db: Session,
    *,
    tenant_id: int,
) -> ShegeHtmlTemplateBinding:
    """幂等创建她格模板投喂源、模板文章及其格式版本。

    手工投喂文章接口目前只接收 Markdown，无法保存 HTML 正文；这里由服务直接
    持久化已审核的 HTML 模板，再调用统一格式模板服务生成蓝图。重复执行复用同一
    来源和文章；当模板 HTML 有升级时由版本化服务创建新格式版本，旧运行不受影响。
    """

    source = (
        db.query(FeedSource)
        .filter(
            FeedSource.tenant_id == tenant_id,
            FeedSource.slug == SHEGE_TEMPLATE_SOURCE_SLUG,
        )
        .first()
    )
    if source is None:
        source = FeedSource(
            tenant_id=tenant_id,
            name=SHEGE_TEMPLATE_SOURCE_NAME,
            slug=SHEGE_TEMPLATE_SOURCE_SLUG,
            source_type="manual",
            source_identifier=SHEGE_TEMPLATE_SOURCE_IDENTIFIER,
            status="active",
            is_active=True,
            style_profile={
                "kind": "system_html_template",
                "brand": "她格",
                "layout": "enterprise-ai-v1",
            },
            fetch_interval_minutes=0,
        )
        db.add(source)
        db.flush()
    else:
        source.name = SHEGE_TEMPLATE_SOURCE_NAME
        source.source_type = "manual"
        source.source_identifier = SHEGE_TEMPLATE_SOURCE_IDENTIFIER
        source.status = "active"
        source.is_active = True
        source.fetch_interval_minutes = 0

    template_html = build_shege_html_template()
    template_article = (
        db.query(FeedSourceArticle)
        .filter(
            FeedSourceArticle.tenant_id == tenant_id,
            FeedSourceArticle.feed_source_id == source.id,
            FeedSourceArticle.title == SHEGE_TEMPLATE_ARTICLE_TITLE,
        )
        .first()
    )
    if template_article is None:
        template_article = FeedSourceArticle(
            tenant_id=tenant_id,
            feed_source_id=source.id,
            title=SHEGE_TEMPLATE_ARTICLE_TITLE,
        )
        db.add(template_article)

    template_article.body_markdown = build_shege_template_markdown()
    template_article.body_html = template_html
    template_article.summary = "她格原创图文的固定 HTML 版式，含四个正文图片槽位。"
    template_article.word_count = len(template_article.body_markdown)
    template_article.is_analyzed = True
    db.flush()

    persisted = create_or_reuse_format_profile(db, article=template_article)
    persisted.profile.is_active = True
    db.flush()
    return ShegeHtmlTemplateBinding(
        feed_source_id=int(source.id),
        feed_article_id=int(template_article.id),
        format_profile_id=int(persisted.profile.id),
    )
