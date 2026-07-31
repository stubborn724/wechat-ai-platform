"""ERP 定时文章的产品与知识库上下文服务。

本模块只负责把“本次选中的产品”和“任务绑定的品牌知识库”转换为 Agent 可消费
的稳定上下文，不负责选图、生成文章或发布。将该职责从 Celery 编排器中拆出后，
可以单独测试知识库为空、标题遗漏产品名等关键边界。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from sqlalchemy.orm import Session

from app.models.pg_models import KbDocumentChunk, KnowledgeBase
from app.schemas.article import ArticleState, SelectedTitle


class ScheduledKnowledgeContextError(RuntimeError):
    """任务绑定的知识库没有可用内容时抛出，阻止无品牌约束的文章继续发布。"""


@dataclass(frozen=True)
class KnowledgePromptContexts:
    """按 Agent 职责切分后的知识库提示词上下文。

    ``article_context`` 只包含文章结构、文案与发布规则；``image_context`` 只包含
    背景、色彩、场景与品牌视觉规则。两者分开存储，防止图片模型消耗正文格式
    token，或正文模型被大段视觉细节干扰。
    """

    article_context: str
    image_context: str


_KNOWLEDGE_SECTION_PATTERN = re.compile(r"【([^】]+)】")
_ARTICLE_SECTION_NAMES = frozenset({
    "文章形式", "文案要求", "发布格式", "内容要求", "标题要求", "排版要求", "末尾联系方式",
})
_IMAGE_SECTION_NAMES = frozenset({
    "图片要求", "背景要求", "视觉要求", "画面要求", "品牌调性", "色彩要求", "材质要求", "场景要求",
})


def split_knowledge_prompt_context(knowledge_context: str) -> KnowledgePromptContexts:
    """将带 ``【章节】`` 标记的知识库内容按文章与图片职责分流。

    标准品牌资料使用“文章形式 / 文案要求 / 图片要求 / 品牌调性”等章节。程序只
    将图片模型实际需要的视觉章节传入 ``image_context``，以降低每张图生图重复
    携带文章格式造成的 token 消耗。未知章节默认归文章侧，保留事实性产品资料的
    正文可用性；完全没有可识别视觉章节的旧资料则回退为同时可用，避免历史任务
    在资料尚未重构前丢失背景约束。
    """

    source = str(knowledge_context or "").strip()
    if not source:
        return KnowledgePromptContexts(article_context="", image_context="")

    matches = list(_KNOWLEDGE_SECTION_PATTERN.finditer(source))
    if not matches:
        # 未按新规范分段的旧知识库无法可靠分类。兼容期内保留原文，后续在
        # 知识库重建后即可自动缩减成各自的精确上下文。
        return KnowledgePromptContexts(article_context=source, image_context=source)

    article_parts: list[str] = []
    image_parts: list[str] = []
    for index, match in enumerate(matches):
        section_name = match.group(1).strip()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        section = source[match.start():section_end].strip()
        if not section:
            continue

        if section_name in _IMAGE_SECTION_NAMES:
            image_parts.append(section)
        elif section_name in _ARTICLE_SECTION_NAMES:
            article_parts.append(section)
        else:
            # 未命名为视觉规则的资料通常是产品事实和品牌说明，优先供文章
            # Agent 使用，避免把冗长文字重复传给每一张图片的生成请求。
            article_parts.append(section)

    article_context = "\n\n".join(article_parts).strip()
    image_context = "\n\n".join(image_parts).strip()
    # 某些旧品牌资料只有“文章形式”或“图片要求”。把唯一侧作为另一侧的保守
    # 回退，保证已启用任务不断流；含完整分区的新资料不会触发该分支。
    if not article_context:
        article_context = image_context
    if not image_context:
        image_context = article_context
    return KnowledgePromptContexts(
        article_context=article_context,
        image_context=image_context,
    )


def compose_knowledge_context(chunks: Iterable[object], max_chars: int = 12_000) -> str:
    """按数据库顺序合并知识库切片，并限制传给 Agent 的最大字符数。

    品牌规则属于强约束，不能再通过空主题做向量检索；因此这里接收知识库的有序
    全量切片。字符上限用于防止未来文档膨胀挤占模型上下文，当前切片会尽量完整
    保留，只有单个切片本身超过剩余额度时才截断。
    """

    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0")

    parts: list[str] = []
    used_chars = 0
    for chunk in chunks:
        content = str(getattr(chunk, "content", "") or "").strip()
        if not content:
            continue
        source = (
            f"[来源: kb_id={getattr(chunk, 'knowledge_base_id', '')}, "
            f"chunk_id={getattr(chunk, 'id', '')}]\n"
        )
        separator = "\n\n---\n\n" if parts else ""
        remaining = max_chars - used_chars - len(separator) - len(source)
        if remaining <= 0:
            break
        part = f"{source}{content[:remaining]}"
        parts.append(part)
        used_chars += len(separator) + len(part)
        if len(content) > remaining:
            break

    return "\n\n---\n\n".join(parts)


def load_required_knowledge_context(
    db: Session,
    knowledge_base_ids: Iterable[int],
    tenant_id: int,
    max_chars: int = 12_000,
) -> str:
    """读取任务绑定知识库的全部有效切片，没有内容时明确失败。

    查询同时校验租户和知识库启用状态，避免旧任务引用已删除知识库，或因错误 ID
    读取其他租户资料。这里不依赖检索主题，因此无主题的投喂仿写任务也能稳定获得
    品牌背景、色调和场景规则。
    """

    normalized_ids = sorted({int(kb_id) for kb_id in knowledge_base_ids if kb_id})
    if not normalized_ids:
        raise ScheduledKnowledgeContextError("定时任务未绑定可用知识库")

    chunks = (
        db.query(KbDocumentChunk)
        .join(KnowledgeBase, KnowledgeBase.id == KbDocumentChunk.knowledge_base_id)
        .filter(
            KbDocumentChunk.knowledge_base_id.in_(normalized_ids),
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.is_active == 1,
        )
        .order_by(
            KbDocumentChunk.knowledge_base_id.asc(),
            KbDocumentChunk.document_id.asc(),
            KbDocumentChunk.chunk_index.asc(),
        )
        .all()
    )
    context = compose_knowledge_context(chunks, max_chars=max_chars)
    if not context:
        raise ScheduledKnowledgeContextError(
            f"知识库 {normalized_ids} 没有可用内容，定时任务已停止发布"
        )
    return context


def bind_product_context(
    state: ArticleState,
    product_name: str,
    configured_topic: str | None,
    article_context: str,
    image_context: str,
    require_article_context: bool = True,
) -> None:
    """把同一产品上下文写入标题、正文和图片 Agent 共用状态。

    产品名必须让标题、正文和图片 Agent 共用；但知识库规则应按职责注入：文章
    Agent 读取版式与文案规则，图片 Agent 读取背景与视觉规则。函数原地更新单次
    任务状态，不持久化参考图字节或其他临时数据。
    """

    normalized_product_name = str(product_name or "").strip()
    normalized_article_context = str(article_context or "").strip()
    normalized_image_context = str(image_context or "").strip()
    if not normalized_product_name:
        raise ValueError("ERP 产品名称不能为空")
    # ERP 图生图始终依赖背景规则。投喂源仿写已经提供文章的原始结构和文案节奏，
    # 因此该模式允许没有文章格式库；没有投喂源的任务仍必须具备文章格式规则。
    if not normalized_image_context:
        raise ScheduledKnowledgeContextError("知识库缺少图片背景生成规则")
    if require_article_context and not normalized_article_context:
        raise ScheduledKnowledgeContextError("知识库缺少文章格式生成规则")

    original_topic = str(configured_topic or "").strip()
    state.product_name = normalized_product_name
    state.topic = (
        f"{normalized_product_name}：{original_topic}"
        if original_topic
        else normalized_product_name
    )
    state.user_description = (
        f"本篇文章的目标产品是“{normalized_product_name}”。主标题必须完整包含该产品名称；"
        "正文遵守文章格式与文案规则，图片仅遵守品牌背景与视觉规则。"
    )
    state.kb_context = normalized_article_context
    state.image_prompt_context = normalized_image_context


def ensure_product_name_in_title(
    selected_title: SelectedTitle,
    product_name: str,
) -> SelectedTitle:
    """保证最终标题包含产品名，模型遗漏时使用稳定前缀补齐。

    提示词约束可以提升自然表达，但不能作为发布规则的唯一保障。这里在 Agent 输出
    边界做确定性校验；已包含产品名的标题保持不变，避免重复拼接。
    """

    normalized_product_name = str(product_name or "").strip()
    if not normalized_product_name:
        raise ValueError("ERP 产品名称不能为空")
    if normalized_product_name in selected_title.main_title:
        return selected_title
    return SelectedTitle(
        main_title=f"{normalized_product_name}｜{selected_title.main_title}",
        sub_title=selected_title.sub_title,
    )
