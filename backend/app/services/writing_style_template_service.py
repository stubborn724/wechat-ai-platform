"""提供可复用写作模板的统一目录。

本模块只维护运营人员可选择的稳定模板编号、展示文案和内部生成规则。定时任务
仅保存模板编号，不把冗长提示词散落在任务记录中，因此同一套写作要求可被多个
任务复用，也能在不改动任务配置的前提下集中优化。
"""

from __future__ import annotations

from dataclasses import dataclass


SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID = "shege_enterprise_ai_service"
ZHONGXIWUJIE_EAST_WEST_LIVING_TEMPLATE_ID = "zhongxiwujie_east_west_living"
XIEHUAI_ORIENTAL_LIVING_TEMPLATE_ID = "xiehuai_oriental_living"
JIANZHI_ARTFUL_LIVING_TEMPLATE_ID = "jianzhi_artful_living"


@dataclass(frozen=True)
class WritingStyleTemplate:
    """一条供任务选择的写作模板。

    ``identifier`` 是数据库中稳定保存的业务键；``label`` 与 ``description``
    面向运营页面；``prompt`` 只供内容生成链路使用，不能通过接口下发给前端。
    """

    identifier: str
    label: str
    description: str
    prompt: str
    # 纯海报文章的公众号标题不应被画面标题长度限制。None 表示沿用格式规则。
    poster_title_max_chars: int | None = None


_SHEGE_ENTERPRISE_AI_SERVICE_PROMPT = """
## 她格企业 AI 服务写作要求
- 标题采用“经营问题或业务动作 | 有结果导向的完整长句”结构，必须围绕中小企业真实经营问题、可获得的业务结果或清晰的落地路径展开；宜为18至30字的完整判断句或设问句，允许使用竖线、冒号、逗号或问号形成阅读节奏。标题要提出明确观点，例如“中小企业用 AI | 不该从买工具开始，而要先找到最耗成本的环节”。禁止使用“AI 入企服务咨询”“解决方案介绍”等菜单式、空泛标题，禁止出现无意义型号、缩写或技术名词堆砌。
- 正文以“一个具体经营痛点 - 可落地的 AI 使用方式 - 推进步骤与判断标准”为主线，语言专业、务实、清楚，不堆砌技术名词。
- 所有观点必须与提供的她格品牌和入企服务知识库一致；信息不足时宁可说明适用边界，也不得虚构客户、数据或交付结果。
- 配图围绕企业团队协同、业务流程、管理决策、客户服务或培训辅导等真实工作场景设计，避免产品陈列、家具卖点和海报式大标题。
- 结尾自然收束到“先识别业务问题，再设计可执行的 AI 落地路径”，不使用夸张承诺或强推销语气。
""".strip()

_ZHONGXIWUJIE_EAST_WEST_LIVING_PROMPT = """
## 中西无界东方奢雅生活标题要求
- 标题采用“产品或品类 | 有审美观点的完整长句”结构，必须自然出现产品名称或明确产品品类，并在此基础上写出东方神韵与当代奢雅生活的关系；不能只写“产品名之境”“产品名美学”等标签式短语。
- 标题可以稍长，建议18至26字，写成有停顿的完整长句，可使用竖线、冒号、逗号或一句有画面的判断；例如“餐桌 | 静奢风的从容，不必依靠浓烈色彩证明存在感”。禁止出现产品型号、SKU 或无意义英文数字。
- 语气贵气、内敛、文化厚重，避免空泛的“高端”“轻奢”“品质生活”与直接推销。
""".strip()

_XIEHUAI_ORIENTAL_LIVING_PROMPT = """
## 写怀东方留白生活标题要求
- 标题采用“产品或品类 | 有审美观点的完整长句”结构，必须自然出现产品名称或明确产品品类，并把线条、材质、比例或居住感写进一句有留白的生活表达；不能只写“产品名之境”“产品名美学”等标签式短语。
- 标题可以稍长，建议18至26字，使用竖线、完整长句、冒号或逗号形成轻缓节奏；例如“奥诺拉沙发 | 静奢风的留白，不必依靠浓烈色彩填满客厅”。禁止出现产品型号、SKU 或无意义英文数字。
- 语气温润、克制、含蓄，有东方文人意境，但不能生僻、堆砌古风词或脱离产品。
""".strip()

_JIANZHI_ARTFUL_LIVING_PROMPT = """
## 剪纸系列当代艺术生活标题要求
- 标题采用“产品或品类 | 有审美观点的完整长句”结构，必须自然出现产品名称或明确产品品类，并从剪纸镂空、光影、花意象、材质或空间体验切入；不能只写“产品名之境”“产品名美学”等标签式短语。
- 标题可以稍长，建议18至26字，写成有画面感的完整长句，可使用竖线、冒号、逗号或一句温柔的判断；例如“餐桌 | 静奢风的克制，让剪纸般的光影留在每一次相聚里”。禁止出现产品型号、SKU 或无意义英文数字。
- 语气当代、艺术、温暖雅致，不夸张，不写促销、型号、参数或模板化口号。
""".strip()


_WRITING_STYLE_TEMPLATES: tuple[WritingStyleTemplate, ...] = (
    WritingStyleTemplate(
        identifier=ZHONGXIWUJIE_EAST_WEST_LIVING_TEMPLATE_ID,
        label="中西无界 - 东方奢雅生活",
        description="用产品承接东方神韵与当代奢雅生活，标题更有文化感和画面感。",
        prompt=_ZHONGXIWUJIE_EAST_WEST_LIVING_PROMPT,
        poster_title_max_chars=26,
    ),
    WritingStyleTemplate(
        identifier=XIEHUAI_ORIENTAL_LIVING_TEMPLATE_ID,
        label="写怀 - 东方留白生活",
        description="围绕产品与安静居住感写作，标题以温润、留白的完整短句呈现。",
        prompt=_XIEHUAI_ORIENTAL_LIVING_PROMPT,
        poster_title_max_chars=26,
    ),
    WritingStyleTemplate(
        identifier=JIANZHI_ARTFUL_LIVING_TEMPLATE_ID,
        label="剪纸系列 - 当代艺术生活",
        description="从产品、光影与剪纸艺术感切入，形成温暖有画面的生活标题。",
        prompt=_JIANZHI_ARTFUL_LIVING_PROMPT,
        poster_title_max_chars=26,
    ),
    WritingStyleTemplate(
        identifier=SHEGE_ENTERPRISE_AI_SERVICE_TEMPLATE_ID,
        label="她格 - 企业 AI 服务",
        description="围绕中小企业经营问题，输出可落地的 AI 转型建议。",
        prompt=_SHEGE_ENTERPRISE_AI_SERVICE_PROMPT,
    ),
)

_TEMPLATE_BY_IDENTIFIER = {
    template.identifier: template for template in _WRITING_STYLE_TEMPLATES
}


def list_writing_style_templates() -> tuple[WritingStyleTemplate, ...]:
    """返回全部可选择模板，顺序即为运营页面展示顺序。"""

    return _WRITING_STYLE_TEMPLATES


def get_writing_style_template(identifier: str | None) -> WritingStyleTemplate | None:
    """按稳定编号解析模板；空值和历史风格值均安全返回空。"""

    normalized_identifier = (identifier or "").strip().lower()
    return _TEMPLATE_BY_IDENTIFIER.get(normalized_identifier)


def get_writing_style_template_prompt(identifier: str | None) -> str:
    """取得模板的生成规则，未选模板时返回空以保持旧任务行为。"""

    template = get_writing_style_template(identifier)
    return template.prompt if template is not None else ""


def get_writing_style_template_title_max_chars(identifier: str | None) -> int | None:
    """返回海报标题的模板级长度上限，未指定时由格式知识库决定。"""

    template = get_writing_style_template(identifier)
    return template.poster_title_max_chars if template is not None else None
