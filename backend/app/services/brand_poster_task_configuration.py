"""新增品牌纯海报定时任务的无密钥配置。

本模块只保存可公开维护的业务规则：品牌来源键、知识库名称、任务时间和海报
数量。微信公众号 AppSecret、ERP client_secret 等凭证由初始化脚本从用户提供的
本地资料读取后加密入库，绝不进入源码或提示词。这样新增品牌只增加一条配置，
不会再复制一套 Agent，也不会触碰已经上线的绣蔓任务。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandPosterTaskConfig:
    """一个品牌的公域/私域海报任务公共配置。"""

    source_key: str
    display_name: str
    format_knowledge_base_name: str
    visual_knowledge_base_name: str
    selection_scope: str
    private_task_name: str
    public_task_name: str
    writing_style_template_id: str
    private_publish_times: tuple[str, ...] = ("08:00", "20:00")
    public_publish_times: tuple[str, ...] = ("13:00",)
    poster_template_total_count: int = 3
    watermark_content: str = ""
    watermark_position: str = "bottom-right"


# 这里只列出本次新增的三个品牌。绣蔓已在正式环境运行，初始化脚本从集合层面
# 排除 xiuman，避免运维人员误把“创建新品牌”理解成“重建全部品牌任务”。
NEW_BRAND_POSTER_CONFIGS: dict[str, BrandPosterTaskConfig] = {
    "zhongxiwujie": BrandPosterTaskConfig(
        source_key="zhongxiwujie",
        display_name="中西无界",
        format_knowledge_base_name="中西无界文章格式规则",
        visual_knowledge_base_name="中西无界背景说明",
        selection_scope="brand:zhongxiwujie",
        private_task_name="中西无界-私域",
        public_task_name="中西无界-公域",
        writing_style_template_id="zhongxiwujie_east_west_living",
        watermark_content="中西无界 TEL:18138381749",
        watermark_position="bottom-left",
    ),
    "xiehuai": BrandPosterTaskConfig(
        source_key="xiehuai",
        display_name="写怀",
        format_knowledge_base_name="写怀文章格式规则",
        visual_knowledge_base_name="写怀背景说明",
        selection_scope="brand:xiehuai",
        private_task_name="写怀-私域",
        public_task_name="写怀-公域",
        writing_style_template_id="xiehuai_oriental_living",
        watermark_content="写怀 TEL:18928694592",
    ),
    "jianzhi": BrandPosterTaskConfig(
        source_key="jianzhi",
        display_name="剪纸系列",
        format_knowledge_base_name="剪纸系列文章格式规则",
        visual_knowledge_base_name="剪纸系列背景说明",
        selection_scope="brand:jianzhi",
        private_task_name="剪纸系列-私域",
        public_task_name="剪纸系列-公域",
        writing_style_template_id="jianzhi_artful_living",
        watermark_content="剪纸系列 TEL:18924894639",
    ),
}


def build_three_image_template_payload() -> dict[str, object]:
    """返回三张主海报模板的持久化数据。

    海报服务的 ``poster_count`` 表示标题海报之外的内容海报数量，因此总数三张
    应保存为二张内容图。联系方式二维码属于固定页脚，不计入主海报数量。
    """

    return {
        "poster_count": 2,
        "seamless": True,
        "total_poster_count": 3,
        # 文字和水印由归档程序叠加，图片模型只负责产品与背景，避免中文错字
        # 和新任务的视觉预览退化为只有朦胧背景。
        "poster_text_overlay_mode": "programmatic_text_v1",
    }
