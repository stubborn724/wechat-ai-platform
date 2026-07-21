"""Agent prompt templates for the article generation pipeline.

These prompts drive the multi-agent article generation workflow:
  Agent 1 -- title brainstorming
  Agent 2 -- outline generation
  Agent 3 -- full content writing
  Agent 4 -- image requirement analysis
  Agent 5 -- image execution / fallback
"""


class PromptConstant:
    """Namespace holding all prompt constants for the article generation pipeline."""

    # -----------------------------------------------------------------------
    # Agent 1 - Title generation
    # -----------------------------------------------------------------------

    AGENT1_TITLE_PROMPT = """你是一个微信公众号文章标题专家。请根据以下主题和风格，生成6个吸引人的标题组合（包含主标题和副标题）。

## 主题
{topic}

## 要求
1. 主标题要吸引眼球，使用数字、悬念、痛点或热点词汇
2. 副标题要对主标题进行补充说明，增加点击欲望
3. 每个组合的主标题和副标题要相互呼应
4. 标题要符合公众号文章的传播规律
5. 避免标题党，要确保标题与内容一致

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "title_options": [
    {{
      "main_title": "主标题1",
      "sub_title": "副标题1"
    }},
    ...
  ]
}}"""

    # -----------------------------------------------------------------------
    # Agent 2 - Outline generation
    # -----------------------------------------------------------------------

    AGENT2_OUTLINE_PROMPT = """你是一个专业的内容策划专家。请根据用户选择的主题、风格和标题，生成一份详细的文章大纲。

## 主题
{topic}

## 选择的标题
主标题：{main_title}
副标题：{sub_title}

## 用户补充描述
{user_description}

## 要求
1. 大纲包含4-6个主要章节
2. 每个章节包含3-5个要点
3. 章节之间要有逻辑递进关系
4. 开头要有吸引力，结尾要有总结升华
5. 大纲要符合所选风格的特点

{style_section}

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "sections": [
    {{
      "section": 1,
      "title": "章节标题",
      "points": ["要点1", "要点2", "要点3"]
    }},
    ...
  ]
}}"""

    AGENT2_DESCRIPTION_SECTION = """
## 用户补充说明
{user_description}
"""

    # -----------------------------------------------------------------------
    # Agent 3 - Full content generation
    # -----------------------------------------------------------------------

    AGENT3_CONTENT_PROMPT = """你是一个专业的微信公众号文章写手。请根据以下大纲和标题，生成一篇完整的公众号文章。

## 标题
{main_title}

## 副标题
{sub_title}

## 大纲
{outline_text}

## 要求
1. 文章长度在1500-2500字之间
2. 段落要短小精悍，适合手机阅读
3. 适当使用emoji表情增加趣味性（每段不超过1个）
4. 在需要配图的位置插入图片占位符，格式为：[IMAGE:position=序号,keywords=关键词,type=cover/section/inline]
5. 开头要吸引人，结尾要有总结和互动引导
6. 文章要有明确的观点和态度
7. 适当使用金句和引用增加文章深度

{style_section}

## 输出格式
请直接输出文章内容，不要包含额外说明。文章内容使用Markdown格式。"""

    # -----------------------------------------------------------------------
    # Agent 4 - Image requirement analysis
    # -----------------------------------------------------------------------

    AGENT4_IMAGE_REQUIREMENTS_PROMPT = """你是一个图片编辑专家。请分析以下文章内容，找出需要配图的位置，并为每个位置生成图片需求。

## 文章标题
{main_title}

## 文章内容
{content}

## 可用图片来源
{enabled_methods_text}

## 要求
1. 封面图：必须有一个封面图（type=cover）
2. 章节配图：每个主要章节至少配一张图（type=section）
3. 内联插图：在文章中间适当位置插入（type=inline）
4. 为每个图片位置提供精准的关键词（英文）
5. 根据内容场景指定最合适的图片来源

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "image_requirements": [
    {{
      "position": 1,
      "type": "cover",
      "section_title": "",
      "image_source": "PEXELS",
      "keywords": "keyword1 keyword2",
      "prompt": "描述性文字，用于图片生成",
      "placeholder_id": "1"
    }},
    ...
  ]
}}"""

    # -----------------------------------------------------------------------
    # Agent 5 - Image execution / fallback
    # -----------------------------------------------------------------------

    AGENT5_IMAGE_EXECUTION_PROMPT = """你是AI图片生成专家。已有一批图片需求，其中部分已通过外部API获取到图片，请为那些没有成功获取图片的位置生成描述用于图片生成。

## 未获取到图片的需求列表
{remaining_requirements}

## 要求
1. 为每个未成功获取图片的位置，生成详细的图片生成描述
2. 描述要包含场景、构图、色彩、风格等细节
3. 确保描述与文章内容高度相关
4. 对于无法生成图片的位置，建议使用合适的占位图

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "image_generations": [
    {{
      "position": 1,
      "type": "section",
      "section_title": "章节标题",
      "description": "详细的图片描述",
      "style": "对应的风格"
    }},
    ...
  ]
}}"""

    # -----------------------------------------------------------------------
    # Style-specific prompt sections
    # -----------------------------------------------------------------------

    STYLE_TECH_PROMPT = """
## 科技风格要求
- 使用专业但不晦涩的技术语言
- 适当引用数据和研究结果
- 结构清晰，逻辑性强
- 可以使用类比帮助理解复杂概念
- 关注最新技术趋势和行业动态
"""

    STYLE_EMOTIONAL_PROMPT = """
## 情感风格要求
- 使用温暖、感性的语言
- 多使用第一人称，增强代入感
- 适当加入个人经历和故事
- 情感表达要真实不做作
- 结尾要有情感升华
"""

    STYLE_EDUCATIONAL_PROMPT = """
## 教育风格要求
- 知识点要准确、实用
- 采用"是什么-为什么-怎么做"的结构
- 多用例子和案例分析
- 每段要有明确的知识点
- 适当设置思考题或互动环节
"""

    STYLE_HUMOROUS_PROMPT = """
## 幽默风格要求
- 使用轻松、活泼的语言
- 适当使用网络热梗和流行语
- 自我调侃增加亲近感
- 笑话要与主题相关
- 幽默要有度，避免冒犯
"""

    # -----------------------------------------------------------------------
    # AI outline modification prompt
    # -----------------------------------------------------------------------

    AI_MODIFY_OUTLINE_PROMPT = """你是一个内容策划专家。请根据用户的修改意见调整文章大纲。

## 原标题
{main_title}

## 原大纲
{outline_text}

## 用户修改意见
{user_feedback}

## 要求
1. 保留原大纲的精华部分
2. 根据用户反馈进行针对性修改
3. 保持章节间的逻辑连贯性

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "sections": [
    {{
      "section": 1,
      "title": "章节标题",
      "points": ["要点1", "要点2", "要点3"]
    }},
    ...
  ]
}}"""

    # -----------------------------------------------------------------------
    # SVG diagram generation prompt
    # -----------------------------------------------------------------------

    SVG_DIAGRAM_GENERATION_PROMPT = """你是一个SVG图表设计专家。请根据以下需求生成一个SVG格式的图表。

## 主题
{topic}

## 描述
{description}

## 要求
1. 生成符合主题的SVG图表
2. 配色美观大方
3. 文字清晰可读
4. SVG代码要完整、可独立运行
5. 图表尺寸推荐800x600

## 输出格式
请直接输出SVG代码，用```svg ... ```包裹，不要包含其他说明。"""

    # -----------------------------------------------------------------------
    # Style mapping
    # -----------------------------------------------------------------------

    STYLE_PROMPT_MAP = {
        "tech": STYLE_TECH_PROMPT,
        "technology": STYLE_TECH_PROMPT,
        "emotional": STYLE_EMOTIONAL_PROMPT,
        "emotion": STYLE_EMOTIONAL_PROMPT,
        "educational": STYLE_EDUCATIONAL_PROMPT,
        "education": STYLE_EDUCATIONAL_PROMPT,
        "humorous": STYLE_HUMOROUS_PROMPT,
        "humor": STYLE_HUMOROUS_PROMPT,
        "funny": STYLE_HUMOROUS_PROMPT,
    }

    @classmethod
    def get_style_prompt(cls, style: str) -> str:
        """Return the style-specific prompt section for a given style key."""
        return cls.STYLE_PROMPT_MAP.get(style.strip().lower(), "")


# Backward-compatible module-level aliases
AGENT1_TITLE_PROMPT = PromptConstant.AGENT1_TITLE_PROMPT
AGENT2_OUTLINE_PROMPT = PromptConstant.AGENT2_OUTLINE_PROMPT
AGENT2_DESCRIPTION_SECTION = PromptConstant.AGENT2_DESCRIPTION_SECTION
AGENT3_CONTENT_PROMPT = PromptConstant.AGENT3_CONTENT_PROMPT
AGENT4_IMAGE_REQUIREMENTS_PROMPT = PromptConstant.AGENT4_IMAGE_REQUIREMENTS_PROMPT
AGENT5_IMAGE_EXECUTION_PROMPT = PromptConstant.AGENT5_IMAGE_EXECUTION_PROMPT
STYLE_TECH_PROMPT = PromptConstant.STYLE_TECH_PROMPT
STYLE_EMOTIONAL_PROMPT = PromptConstant.STYLE_EMOTIONAL_PROMPT
STYLE_EDUCATIONAL_PROMPT = PromptConstant.STYLE_EDUCATIONAL_PROMPT
STYLE_HUMOROUS_PROMPT = PromptConstant.STYLE_HUMOROUS_PROMPT
AI_MODIFY_OUTLINE_PROMPT = PromptConstant.AI_MODIFY_OUTLINE_PROMPT
SVG_DIAGRAM_GENERATION_PROMPT = PromptConstant.SVG_DIAGRAM_GENERATION_PROMPT
STYLE_PROMPT_MAP = PromptConstant.STYLE_PROMPT_MAP


def get_style_prompt(style: str) -> str:
    """Return the style-specific prompt section for a given style key."""
    return PromptConstant.get_style_prompt(style)

# ---------------------------------------------------------------------------
# Agent 2 - Outline generation
# ---------------------------------------------------------------------------

AGENT2_OUTLINE_PROMPT = """你是一个专业的内容策划专家。请根据用户选择的主题、风格和标题，生成一份详细的文章大纲。

## 主题
{topic}

## 选择的标题
主标题：{main_title}
副标题：{sub_title}

## 风格
{style}

## 用户补充描述
{user_description}

## 要求
1. 大纲包含4-6个主要章节
2. 每个章节包含3-5个要点
3. 章节之间要有逻辑递进关系
4. 开头要有吸引力，结尾要有总结升华
5. 大纲要符合所选风格的特点

{style_section}

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "sections": [
    {{
      "section": 1,
      "title": "章节标题",
      "points": ["要点1", "要点2", "要点3"]
    }},
    ...
  ]
}}"""

AGENT2_DESCRIPTION_SECTION = """
## 用户补充说明
{user_description}
"""

# ---------------------------------------------------------------------------
# Agent 3 - Full content generation
# ---------------------------------------------------------------------------

AGENT3_CONTENT_PROMPT = """你是一个专业的微信公众号文章写手。请根据以下大纲和标题，生成一篇完整的公众号文章。

## 标题
{main_title}

## 副标题
{sub_title}

## 风格
{style}

## 大纲
{outline_text}

## 要求
1. 文章长度在1500-2500字之间
2. 语言要符合所选风格
3. 段落要短小精悍，适合手机阅读
4. 适当使用emoji表情增加趣味性（每段不超过1个）
5. 在需要配图的位置插入图片占位符，格式为：[IMAGE:position=序号,keywords=关键词,type=cover/section/inline]
6. 开头要吸引人，结尾要有总结和互动引导
7. 文章要有明确的观点和态度
8. 适当使用金句和引用增加文章深度

{style_section}

## 输出格式
请直接输出文章内容，不要包含额外说明。文章内容使用Markdown格式。"""

# ---------------------------------------------------------------------------
# Agent 4 - Image requirement analysis
# ---------------------------------------------------------------------------

AGENT4_IMAGE_REQUIREMENTS_PROMPT = """你是一个图片编辑专家。请分析以下文章内容，找出需要配图的位置，并为每个位置生成图片需求。

## 文章标题
{main_title}

## 文章内容
{content}

## 可用图片来源
{enabled_methods_text}

## 要求
1. 封面图：必须有一个封面图（type=cover）
2. 章节配图：每个主要章节至少配一张图（type=section）
3. 内联插图：在文章中间适当位置插入（type=inline）
4. 为每个图片位置提供精准的关键词（英文）
5. 根据内容场景指定最合适的图片来源

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "image_requirements": [
    {{
      "position": 1,
      "type": "cover",
      "section_title": "",
      "image_source": "PEXELS",
      "keywords": "keyword1 keyword2",
      "prompt": "描述性文字，用于图片生成",
      "placeholder_id": "1"
    }},
    ...
  ]
}}"""

# ---------------------------------------------------------------------------
# Agent 5 - Image execution / fallback
# ---------------------------------------------------------------------------

AGENT5_IMAGE_EXECUTION_PROMPT = """你是AI图片生成专家。已有一批图片需求，其中部分已通过外部API获取到图片，请为那些没有成功获取图片的位置生成描述用于图片生成。

## 未获取到图片的需求列表
{remaining_requirements}

## 要求
1. 为每个未成功获取图片的位置，生成详细的图片生成描述
2. 描述要包含场景、构图、色彩、风格等细节
3. 确保描述与文章内容高度相关
4. 对于无法生成图片的位置，建议使用合适的占位图

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "image_generations": [
    {{
      "position": 1,
      "type": "section",
      "section_title": "章节标题",
      "description": "详细的图片描述",
      "style": "对应的风格"
    }},
    ...
  ]
}}"""

# ---------------------------------------------------------------------------
# Style-specific prompt sections
# ---------------------------------------------------------------------------

STYLE_TECH_PROMPT = """
## 科技风格要求
- 使用专业但不晦涩的技术语言
- 适当引用数据和研究结果
- 结构清晰，逻辑性强
- 可以使用类比帮助理解复杂概念
- 关注最新技术趋势和行业动态
"""

STYLE_EMOTIONAL_PROMPT = """
## 情感风格要求
- 使用温暖、感性的语言
- 多使用第一人称，增强代入感
- 适当加入个人经历和故事
- 情感表达要真实不做作
- 结尾要有情感升华
"""

STYLE_EDUCATIONAL_PROMPT = """
## 教育风格要求
- 知识点要准确、实用
- 采用"是什么-为什么-怎么做"的结构
- 多用例子和案例分析
- 每段要有明确的知识点
- 适当设置思考题或互动环节
"""

STYLE_HUMOROUS_PROMPT = """
## 幽默风格要求
- 使用轻松、活泼的语言
- 适当使用网络热梗和流行语
- 自我调侃增加亲近感
- 笑话要与主题相关
- 幽默要有度，避免冒犯
"""

# ---------------------------------------------------------------------------
# AI outline modification prompt
# ---------------------------------------------------------------------------

AI_MODIFY_OUTLINE_PROMPT = """你是一个内容策划专家。请根据用户的修改意见调整文章大纲。

## 原标题
{main_title}

## 原大纲
{outline_text}

## 用户修改意见
{user_feedback}

## 要求
1. 保留原大纲的精华部分
2. 根据用户反馈进行针对性修改
3. 保持章节间的逻辑连贯性

## 输出格式
请严格按以下JSON格式输出，不要包含其他内容：
{{
  "sections": [
    {{
      "section": 1,
      "title": "章节标题",
      "points": ["要点1", "要点2", "要点3"]
    }},
    ...
  ]
}}"""

# ---------------------------------------------------------------------------
# SVG diagram generation prompt
# ---------------------------------------------------------------------------

SVG_DIAGRAM_GENERATION_PROMPT = """你是一个SVG图表设计专家。请根据以下需求生成一个SVG格式的图表。

## 主题
{topic}

## 描述
{description}

## 要求
1. 生成符合主题的SVG图表
2. 配色美观大方
3. 文字清晰可读
4. SVG代码要完整、可独立运行
5. 图表尺寸推荐800x600

## 输出格式
请直接输出SVG代码，用```svg ... ```包裹，不要包含其他说明。"""

# ---------------------------------------------------------------------------
# Mapping for style-specific prompt sections
# ---------------------------------------------------------------------------

STYLE_PROMPT_MAP = {
    "tech": STYLE_TECH_PROMPT,
    "technology": STYLE_TECH_PROMPT,
    "emotional": STYLE_EMOTIONAL_PROMPT,
    "emotion": STYLE_EMOTIONAL_PROMPT,
    "educational": STYLE_EDUCATIONAL_PROMPT,
    "education": STYLE_EDUCATIONAL_PROMPT,
    "humorous": STYLE_HUMOROUS_PROMPT,
    "humor": STYLE_HUMOROUS_PROMPT,
    "funny": STYLE_HUMOROUS_PROMPT,
}


def get_style_prompt(style: str) -> str:
    """Return the style-specific prompt section for a given style key."""
    return STYLE_PROMPT_MAP.get(style.strip().lower(), "")
