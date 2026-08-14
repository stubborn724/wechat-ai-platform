"""ERP 定时文章的公众号标题规范化服务。

格式模板任务会将标题和正文槽位合并到一次文本调用中。模型偶尔漏填
``wechat_title``，旧流程便把 ERP 型号直接写入公众号标题。本模块只依赖已经
确定的产品场景档案，在不增加模型调用的前提下提供可发布的标题兜底。
"""

from __future__ import annotations

import re

from app.services.scheduled_product_scene_service import ProductSceneProfile


_GENERIC_PRODUCT_TERMS = ("家具单品", "家居产品", "未识别家具", "未命名产品")
_TITLE_BODY_BY_SCENE_KEY = {
    "bed": "东方神韵与当代奢雅，在静谧卧室里安放一夜从容",
    "sofa": "东方神韵与当代奢雅，在从容客厅里修养日常",
    "coffee_table": "东方神韵与当代奢雅，在客厅光影里修养日常",
    "dining_table": "东方神韵与当代奢雅，在每一次相聚里安放从容",
    "dining_chair": "东方神韵与当代奢雅，在相聚时刻留住从容",
    "dining_sideboard": "东方神韵与当代奢雅，在餐叙之间收纳从容",
    "tv_cabinet": "东方神韵与当代奢雅，在客厅光影里安放秩序",
    "wardrobe": "东方神韵与当代奢雅，在卧室日常里收纳从容",
    "desk": "东方神韵与当代奢雅，在安静书房里沉淀日常",
}


def normalize_scheduled_product_title(
    product_name: str,
    *,
    profile: ProductSceneProfile,
    candidate_title: str | None,
) -> str:
    """将 ERP 标题收敛为“产品品类|完整审美长句”。

    优先保留模型返回的有效长句，避免所有文章使用同一个兜底标题；但 SKU、
    “家具单品”等占位名、空标题，以及写入错误房间的标题都会被替换。产品品类
    来自同一份场景档案，床类自然只会落入卧室语义，不会再出现“床在客厅”。
    """

    subject = _resolve_title_subject(product_name, profile)
    body = _extract_title_body(candidate_title or "", product_name)
    if not _is_valid_title_body(body, profile):
        body = _TITLE_BODY_BY_SCENE_KEY.get(
            profile.key,
            "东方神韵与当代奢雅，在日常里修养从容",
        )
    return f"{subject}|{body}"


def _resolve_title_subject(product_name: str, profile: ProductSceneProfile) -> str:
    """从展示名取可读品类，SKU 或通用占位名则回退场景档案。"""

    normalized = str(product_name or "").strip()
    normalized = re.sub(r"^[A-Za-z]{1,}[A-Za-z0-9_-]*", "", normalized)
    normalized = re.sub(r"[\s·、,，;；:：|｜]+", "", normalized)
    for generic_term in _GENERIC_PRODUCT_TERMS:
        normalized = normalized.replace(generic_term, "")
    chinese_only = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    if 1 <= len(chinese_only) <= 12:
        return chinese_only
    return str(profile.label).split("/", 1)[0].strip() or "家居单品"


def _extract_title_body(candidate_title: str, product_name: str) -> str:
    """提取标题右侧的审美长句，移除 SKU 与产品占位名。"""

    normalized = " ".join(str(candidate_title or "").split()).strip(" |｜")
    if not normalized:
        return ""
    parts = [part.strip() for part in re.split(r"[|｜]", normalized) if part.strip()]
    body = parts[-1] if len(parts) > 1 else normalized
    body = body.replace(str(product_name or "").strip(), "")
    body = re.sub(r"[A-Za-z]{1,}[A-Za-z0-9_-]*", "", body)
    for generic_term in _GENERIC_PRODUCT_TERMS:
        body = body.replace(generic_term, "")
    return body.strip(" ，,、；;。.:：")


def _is_valid_title_body(body: str, profile: ProductSceneProfile) -> bool:
    """判断模型标题是否具备可读性且不包含产品场景冲突。"""

    normalized = str(body or "").strip()
    if len(normalized) < 8:
        return False
    return not any(term in normalized for term in profile.forbidden_elements)
