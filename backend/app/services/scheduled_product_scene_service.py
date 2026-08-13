"""ERP 产品与生成场景的通用匹配策略。

定时 ERP 图片过去只把品牌的“现代家居”提示词和模型生成的槽位描述拼接起来，
模型因此可能把餐桌放进客厅、把床放进餐厅。这个模块把“产品是什么”和“应该
出现在哪个空间”收敛成一次确定性的程序决策：

* 读取 ERP 产品名称、分类和标签，不调用新的大模型；
* 为常见家具提供可扩展的空间、允许陈设和禁止陈设；
* 在槽位文本进入图片模型前清理冲突词，并在最终提示词末尾追加不可覆盖的硬约束；
* 未识别品类使用保守的通用规则，避免错误地把产品强行归到某个房间。

该服务只由 ERP 定时图生图链路调用。普通投喂源 HTML 仿写、纯海报渲染和已经
生成的历史文章不会读取这里的策略，因此不会产生历史内容污染。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Any


PRODUCT_SCENE_GUARD_MARKER = "【产品-场景一致性硬约束】"


@dataclass(frozen=True)
class ProductSceneProfile:
    """一个产品类别对应的空间规则。

    ``trigger_terms`` 只用于程序识别，不会直接暴露给图片模型。其余字段会被
    编译成简短的正向/反向提示词，保证不同图片槽位复用同一业务边界。
    """

    key: str
    label: str
    trigger_terms: tuple[str, ...]
    required_rooms: tuple[str, ...]
    allowed_elements: tuple[str, ...]
    forbidden_elements: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        """转换为可安全放入 ``ArticleState`` 的普通 JSON 字典。"""

        return {
            "key": self.key,
            "label": self.label,
            "required_rooms": list(self.required_rooms),
            "allowed_elements": list(self.allowed_elements),
            "forbidden_elements": list(self.forbidden_elements),
        }


# 顺序只作为同长度命中时的稳定兜底。更具体的触发词通过最长匹配优先，避免
# “餐边柜”先被通用“柜”或“边几”误判。新增家具类别只需在这里增加配置，不需要
# 新建 Agent 或修改文章生成流程。
_PRODUCT_SCENE_PROFILES: tuple[ProductSceneProfile, ...] = (
    ProductSceneProfile(
        key="dining_sideboard",
        label="餐边柜/餐厅收纳",
        trigger_terms=("餐边柜", "餐边台", "餐厅边柜"),
        required_rooms=("餐厅", "独立用餐区"),
        allowed_elements=("餐桌", "餐椅", "餐具", "吊灯", "餐厅墙面", "餐区地面"),
        forbidden_elements=("客厅沙发", "电视", "电视柜", "茶几", "床", "床头柜"),
    ),
    ProductSceneProfile(
        key="tv_cabinet",
        label="电视柜/客厅收纳",
        trigger_terms=("电视柜", "影视柜", "电视墙柜"),
        required_rooms=("客厅", "家庭影音区"),
        allowed_elements=("电视墙", "沙发", "地毯", "边几", "绿植", "客厅地面"),
        forbidden_elements=("餐桌", "餐椅", "床", "床头柜", "厨房岛台"),
    ),
    ProductSceneProfile(
        key="kitchen_island",
        label="厨房岛台/厨房操作台",
        trigger_terms=("厨房岛台", "中岛台", "厨房操作台", "中岛"),
        required_rooms=("厨房", "开放式厨房"),
        allowed_elements=("橱柜", "水槽", "厨房吊灯", "吧椅", "备餐用品"),
        forbidden_elements=("客厅沙发", "电视", "床", "床头柜", "餐厅餐桌"),
    ),
    ProductSceneProfile(
        key="dining_table",
        label="餐桌/用餐家具",
        trigger_terms=("餐桌", "餐台", "饭桌", "用餐桌", "圆餐桌"),
        required_rooms=("餐厅", "独立用餐区"),
        allowed_elements=("餐椅", "餐边柜", "吊灯", "餐具", "餐厅墙面", "餐区地面"),
        forbidden_elements=(
            "客厅",
            "沙发",
            "客厅沙发",
            "电视",
            "电视柜",
            "茶几",
            "床",
            "床头柜",
            "办公桌",
            "书桌",
            "厨房岛台",
        ),
    ),
    ProductSceneProfile(
        key="dining_chair",
        label="餐椅/用餐座椅",
        trigger_terms=("餐椅", "餐凳", "用餐椅"),
        required_rooms=("餐厅", "独立用餐区"),
        allowed_elements=("餐桌", "餐边柜", "吊灯", "餐具", "餐厅地面"),
        forbidden_elements=("客厅沙发", "电视", "电视柜", "床", "床头柜", "办公桌"),
    ),
    ProductSceneProfile(
        key="bed",
        label="床/卧室家具",
        trigger_terms=("软包床", "床架", "双人床", "单人床", "床头", "床"),
        required_rooms=("卧室", "睡眠空间"),
        allowed_elements=("床头柜", "床品", "窗帘", "壁灯", "衣柜", "卧室地面"),
        # 床品场景必须是独立卧室。只禁“客厅沙发”会遗漏模型常写的“沙发”、
        # “贵妃榻”等同类陈设，最终造成卧室和客厅家具混搭。
        forbidden_elements=(
            "客厅沙发",
            "客厅",
            "沙发",
            "贵妃榻",
            "休闲榻",
            "电视柜",
            "餐桌",
            "餐椅",
            "茶几",
            "厨房岛台",
        ),
    ),
    ProductSceneProfile(
        key="sofa",
        label="沙发/客厅座椅",
        trigger_terms=("沙发", "贵妃榻", "休闲榻", "组合沙发"),
        required_rooms=("客厅", "休闲起居区"),
        allowed_elements=("茶几", "边几", "地毯", "落地灯", "电视墙", "抱枕"),
        forbidden_elements=("餐桌", "餐椅", "床", "床头柜", "厨房岛台", "办公桌"),
    ),
    ProductSceneProfile(
        key="coffee_table",
        label="茶几/边几",
        trigger_terms=("茶几", "边几", "角几", "边桌", "咖啡桌"),
        required_rooms=("客厅", "休闲起居区"),
        allowed_elements=("沙发", "地毯", "落地灯", "托盘", "客厅地面"),
        forbidden_elements=("餐厅餐桌", "床", "床头柜", "厨房岛台", "办公桌"),
    ),
    ProductSceneProfile(
        key="desk",
        label="书桌/办公桌",
        trigger_terms=("书桌", "办公桌", "写字台", "电脑桌", "工作台"),
        required_rooms=("书房", "家庭办公区"),
        allowed_elements=("办公椅", "书架", "台灯", "电脑", "文件收纳"),
        forbidden_elements=("客厅沙发", "电视", "餐桌", "餐椅", "床", "床头柜"),
    ),
    ProductSceneProfile(
        key="wardrobe",
        label="衣柜/卧室收纳",
        trigger_terms=("衣柜", "衣橱", "衣帽柜", "衣物柜"),
        required_rooms=("卧室", "衣帽间"),
        allowed_elements=("床", "床头柜", "穿衣镜", "衣物收纳", "卧室地面"),
        forbidden_elements=("客厅沙发", "电视", "餐桌", "餐椅", "厨房岛台"),
    ),
    ProductSceneProfile(
        key="bookcase",
        label="书柜/展示架",
        trigger_terms=("书柜", "书架", "展示架", "展示柜", "置物架"),
        required_rooms=("书房", "客厅阅读区"),
        allowed_elements=("书籍", "装饰品", "阅读椅", "台灯", "墙面"),
        forbidden_elements=("餐桌", "餐椅", "床", "床头柜", "厨房岛台"),
    ),
    ProductSceneProfile(
        key="generic_furniture",
        label="未识别家具",
        trigger_terms=(),
        required_rooms=("与产品功能匹配的真实家居空间",),
        allowed_elements=("与产品功能直接相关的少量配套家具", "墙面", "地面", "自然光影"),
        forbidden_elements=("不属于产品功能空间的大型家具", "无关产品", "混杂的多种房间布置"),
    ),
)

_PROFILES_BY_KEY = {profile.key: profile for profile in _PRODUCT_SCENE_PROFILES}


def _iter_text_values(values: Iterable[str] | str | None) -> Iterable[str]:
    """统一处理 ERP 返回的列表和单个字符串，过滤空值并保留原始语义。"""

    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return (str(value) for value in values if value is not None)


def _normalize_signal(value: str) -> str:
    """去除空格和常见分隔符，降低 ERP 中英文/中文命名差异的影响。"""

    return re.sub(r"[\s_\-/,，。；;:：()（）]+", "", str(value or "").lower())


def resolve_product_scene_profile(
    product_name: str,
    *,
    tags: Iterable[str] | str | None = None,
    categories: Iterable[str] | str | None = None,
) -> ProductSceneProfile:
    """根据产品名称、ERP 标签和分类选择最具体的空间规则。

    名称和 ERP 分类优先于泛化标签；命中多个类别时选择最长触发词，避免“餐边柜”
    先落入“柜类”或“边几”规则。完全未知时返回保守通用配置，不凭空指定房间。
    """

    signals = [_normalize_signal(product_name)]
    signals.extend(_normalize_signal(value) for value in _iter_text_values(categories))
    signals.extend(_normalize_signal(value) for value in _iter_text_values(tags))
    signals = [signal for signal in signals if signal]

    best_profile = _PROFILES_BY_KEY["generic_furniture"]
    best_score = 0
    best_order = len(_PRODUCT_SCENE_PROFILES)
    for order, profile in enumerate(_PRODUCT_SCENE_PROFILES):
        for trigger in profile.trigger_terms:
            normalized_trigger = _normalize_signal(trigger)
            if not normalized_trigger:
                continue
            if not any(normalized_trigger in signal for signal in signals):
                continue
            score = len(normalized_trigger)
            if score > best_score or (score == best_score and order < best_order):
                best_profile = profile
                best_score = score
                best_order = order
    return best_profile


def product_scene_profile_from_payload(
    payload: object,
    *,
    product_name: str = "",
    tags: Iterable[str] | str | None = None,
    categories: Iterable[str] | str | None = None,
) -> ProductSceneProfile:
    """从 ``ArticleState`` 的 JSON 快照恢复规则，异常快照回退到确定性识别。"""

    if isinstance(payload, dict):
        key = str(payload.get("key") or "").strip()
        profile = _PROFILES_BY_KEY.get(key)
        if profile is not None:
            return profile
    return resolve_product_scene_profile(product_name, tags=tags, categories=categories)


def _clean_residual_connectors(text: str) -> str:
    """清掉删除冲突词后残留的“与/旁边”等连接词，避免生成半截语句。"""

    cleaned = re.sub(
        r"(?:放在|置于|位于)\s*(?:旁|附近|一侧)(?:的)?",
        "",
        text,
    )
    cleaned = re.sub(r"(?:旁边|旁的|附近|靠近)(?:的)?", "", cleaned)
    cleaned = re.sub(
        r"(?:与|和|及|搭配|组合|配套)\s*(?=[，,、；;。.!！?？\s]|$)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"([，,、；;])\s*(?=[，,、；;])", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ，,、；;。")


def sanitize_product_scene_text(
    text: str,
    profile: ProductSceneProfile,
    *,
    fallback_subject: str = "",
) -> str:
    """从模型槽位描述中移除与产品功能冲突的空间和家具词。

    这是提示词进入图片模型前的最后一道文本边界。它不改产品名称和主体结构，
    只清理模型误写的场景元素；当清理后内容过短时，用产品名和正确空间作为可读
    兜底，避免 HTML ``alt`` 和图片提示词变成空字符串。
    """

    cleaned = str(text or "").strip()
    for forbidden in sorted(profile.forbidden_elements, key=len, reverse=True):
        if forbidden and not forbidden.startswith("不属于") and not forbidden.startswith("无关"):
            cleaned = cleaned.replace(forbidden, "")
    cleaned = _clean_residual_connectors(cleaned)

    subject = str(fallback_subject or "").strip()
    if len(cleaned) < 2:
        cleaned = subject or profile.label
    elif subject and subject not in cleaned:
        # 关键词 Agent 有时只返回“餐桌/床”等泛称。补回本次 ERP 已选产品名，
        # 让 alt、图片提示词和正文实际产品保持同一主体，不让模型重新猜产品。
        cleaned = f"{subject}，{cleaned}"
    room = profile.required_rooms[0]
    if room not in cleaned:
        cleaned = f"{cleaned}，{room}场景"
    return cleaned


def append_product_scene_guard(
    prompt: str,
    profile: ProductSceneProfile,
    *,
    product_name: str = "",
) -> str:
    """为最终图生图提示词追加一次产品-场景正反向硬约束。"""

    normalized_prompt = str(prompt or "").strip()
    if PRODUCT_SCENE_GUARD_MARKER in normalized_prompt:
        return normalized_prompt

    product_label = str(product_name or "").strip() or profile.label
    rooms = "、".join(profile.required_rooms)
    allowed = "、".join(profile.allowed_elements)
    forbidden = "、".join(profile.forbidden_elements)
    guard = (
        f"{PRODUCT_SCENE_GUARD_MARKER}\n"
        f"目标产品：{product_label}\n"
        f"产品类别：{profile.label}\n"
        f"必须场景：{rooms}\n"
        f"优先陈设：{allowed}\n"
        f"禁止出现：{forbidden}\n"
        "空间一致性要求：主体必须真实放置在上述功能空间中，陈设只服务于该空间和"
        "产品使用关系；不要把不同房间的大型家具混入同一画面，不要用客厅布置代替"
        "餐厅、用餐区或其他产品明确要求的功能空间。"
    )
    if profile.key == "bed":
        guard += (
            "床品专属约束：画面只能是卧室或睡眠空间，不得出现沙发、贵妃榻、"
            "休闲榻、客厅或任何客厅会客区陈设。"
        )
    elif profile.key == "sofa":
        guard += (
            "沙发专属约束：画面只能是客厅或休闲起居区，不得出现餐桌、餐椅、"
            "餐边柜、厨房岛台或卧室床具。"
        )
    elif profile.key == "dining_table":
        guard += (
            "餐桌专属约束：画面只能是餐厅或独立用餐区，不得出现床、床头柜、"
            "卧室、沙发、电视柜或客厅休闲区陈设。"
        )
    return f"{normalized_prompt}\n\n{guard}" if normalized_prompt else guard


def append_erp_image_viewpoint_instruction(
    prompt: str,
    position: int,
    *,
    total: int,
) -> str:
    """为 ERP 同篇配图分配稳定且互不重复的镜头视角。

    该函数只负责把镜头差异和联系方式禁生约束编译到提示词，不调用模型，也不
    改变产品主体。将规则放在生图请求的最后一层，是为了覆盖上游知识库或 Agent
    偶尔写入的联系方式描述；电话、二维码和品牌文字由程序页脚/水印渲染，绝不能
    让图像模型自行绘制，避免出现联系方式海报幻觉。
    """

    normalized_prompt = str(prompt or "").strip()
    safe_total = max(1, int(total or 1))
    safe_position = max(1, int(position or 1))
    view_index = (safe_position - 1) % 5
    viewpoints = (
        "正面三分之四主视角：展示产品完整轮廓、正面比例和主要使用关系。",
        "反向三分之四视角：从产品另一侧观察，展示侧面厚度、背部结构和空间关系。",
        "侧面视角：以低机位侧拍展示产品的纵深、边缘线条、支撑结构和落地关系。",
        "材质细节视角：近距离聚焦产品材质、纹理、连接件或边角工艺，同时保留可识别主体。",
        "生活空间广角：从更远的环境视角展示产品在正确功能空间中的完整陈设关系。",
    )
    instruction = (
        "【ERP 多角度硬约束】\n"
        f"本篇共 {safe_total} 张产品图，本图为第 {safe_position} 张。\n"
        f"{viewpoints[view_index]}\n"
        "同一篇图片必须保持同一产品、同一空间类型和一致色温，但每张必须改变镜头位置、"
        "取景距离或构图；禁止复制、镜像、简单裁切或只放大上一张。\n"
        "【联系方式隔离硬约束】\n"
        "图像模型禁止生成任何可读文字、中文、英文、数字、电话号码、二维码、微信图标、"
        "抖音图标、品牌名称、Logo、水印、产品咨询卡或联系方式背景。联系方式只允许由"
        "程序在文章固定底部咨询卡中渲染；如任务配置了图片水印，也只能由程序后处理叠加。"
    )
    if "【ERP 多角度硬约束】" in normalized_prompt:
        return normalized_prompt
    return f"{normalized_prompt}\n\n{instruction}" if normalized_prompt else instruction
