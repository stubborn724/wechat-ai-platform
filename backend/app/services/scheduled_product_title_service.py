"""ERP 定时文章的公众号标题规范化服务。

格式模板任务会将标题和正文槽位合并到一次文本调用中。模型偶尔漏填
``wechat_title``，旧流程便把 ERP 型号直接写入公众号标题。本模块只依赖已经
确定的产品场景档案，在不增加模型调用的前提下提供可发布的标题兜底。
"""

from __future__ import annotations

import re

from app.services.scheduled_product_scene_service import ProductSceneProfile


_GENERIC_PRODUCT_TERMS = ("家具单品", "家居产品", "未识别家具", "未命名产品")
_PUBLIC_FALLBACK_SUBJECT_BY_SCENE_KEY = {
    # ``generic_furniture`` 是场景服务的内部兜底名，用来表达“不要猜测房间”，
    # 不能透传到公众号标题，否则会把内部识别状态展示给读者。
    "generic_furniture": "家居美学",
}
_ORIENTAL_TITLE_BODY_BY_SCENE_KEY = {
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
_XIUMAN_MODERN_TITLE_BODY_BY_SCENE_KEY = {
    "bed": "以柔和比例与舒适尺度，留住卧室的安静日常",
    "sofa": "用松弛尺度和干净线条，安放客厅的日常停留",
    "coffee_table": "让现代材质与比例在客厅光线里形成轻松平衡",
    "dining_table": "以清晰尺度承接每一次自在相聚",
    "dining_chair": "让舒适坐感自然融入每一次相聚",
    "dining_sideboard": "用简洁收纳让用餐空间更从容有序",
    "tv_cabinet": "以干净比例整理客厅的视觉秩序",
    "wardrobe": "以实用收纳和舒适尺度整理卧室日常",
    "desk": "以简洁线条留出专注而松弛的工作日常",
}
_XIUMAN_DISALLOWED_TITLE_TERMS = ("东方", "奢雅", "禅意", "国风", "新中式")


def normalize_scheduled_product_title(
    product_name: str,
    *,
    profile: ProductSceneProfile,
    candidate_title: str | None,
    brand_key: str | None = None,
) -> str:
    """将 ERP 标题收敛为“产品品类|完整审美长句”。

    优先保留模型返回的有效长句，避免所有文章使用同一个兜底标题；但 SKU、
    “家具单品”等占位名、空标题，以及写入错误房间的标题都会被替换。产品品类
    来自同一份场景档案，床类自然只会落入卧室语义，不会再出现“床在客厅”。
    """

    normalized_brand_key = str(brand_key or "").strip().lower()
    subject = _resolve_title_subject(product_name, profile)
    body = _extract_title_body(candidate_title or "", product_name)
    if not _is_valid_title_body(body, profile, brand_key=normalized_brand_key):
        body = _fallback_title_body(profile, brand_key=normalized_brand_key)
    return f"{subject}|{body}"


def _fallback_title_body(profile: ProductSceneProfile, *, brand_key: str) -> str:
    """按已冻结的 ERP 品牌选择标题兜底，避免跨品牌审美词串用。"""

    if brand_key == "xiuman":
        return _XIUMAN_MODERN_TITLE_BODY_BY_SCENE_KEY.get(
            profile.key,
            "以现代材质与舒适比例，回应真实的居住日常",
        )
    return _ORIENTAL_TITLE_BODY_BY_SCENE_KEY.get(
        profile.key,
        "东方神韵与当代奢雅，在日常里修养从容",
    )


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
    public_fallback = _PUBLIC_FALLBACK_SUBJECT_BY_SCENE_KEY.get(profile.key)
    if public_fallback:
        return public_fallback
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


def _is_valid_title_body(
    body: str,
    profile: ProductSceneProfile,
    *,
    brand_key: str,
) -> bool:
    """判断模型标题是否具备可读性且不包含产品场景冲突。"""

    normalized = str(body or "").strip()
    if len(normalized) < 8:
        return False
    if any(term in normalized for term in profile.forbidden_elements):
        return False
    if brand_key == "xiuman" and any(
        term in normalized for term in _XIUMAN_DISALLOWED_TITLE_TERMS
    ):
        return False
    return True
