# HTML 版式仿写设计

## 目标

让仿写任务可选用参考公众号文章的原始 HTML 版式。程序保留 DOM、行内样式与图片位置，内容 Agent 仅生成文字槽位和图片槽位内容；既有 Markdown 仿写的行为保持不变。

## 现状与问题

主文章生成入口已经通过 `ArticleState.reference_html`、`agent3_generate_html_imitation_content` 和 `html_imitation_service` 支持 HTML 模板回填。仿写任务入口 `execute_imitation_generation` 只读取 `FeedSourceArticle.body_markdown`，因此不会设置 `reference_html`，最终仍走旧的 Markdown 正文生成路径。

## 方案

在 `ImitationTask` 增加持久化字段 `imitation_mode`：

- `content`：默认值，沿用当前 Markdown 仿写。
- `html_layout`：要求所选参考文章具有非空 `body_html`，将原始 HTML 写入 `ArticleState.reference_html`。

任务执行时，结构分析 Agent 仍使用 Markdown，产出文章写作结构和语气指导；HTML 版式由 `html_imitation_service` 以确定性 DOM 解析完成。随后现有内容 Agent 分支仅输出槽位 JSON，服务层回填到不可变模板，并由现有图片流程在原 `img` 节点替换生成图。

## 接口与界面

任务创建、查询响应和前端任务表单暴露 `imitation_mode`。创建界面提供“内容结构仿写”和“HTML 版式仿写”两个明确选项，后者说明其使用投喂文章的原始视觉版式。任务列表显示当前模式，方便确认运行行为。

## 失败与兼容

`html_layout` 模式下，选中的投喂源没有 HTML 时任务立即失败并返回明确原因，不回退到 Markdown，避免用户误以为版式被保留。旧任务数据库记录因默认值继续按 `content` 执行。数据库升级通过项目既有 MySQL 初始化/迁移策略补充该字段。

## 测试

测试覆盖：默认模式不写入 `reference_html`；HTML 模式传入原始 `body_html` 并触发现有 HTML 内容生成分支；缺少 HTML 的 HTML 模式拒绝执行；创建接口和前端请求携带模式字段。已有 `html_imitation_service` 单元测试继续验证 DOM、样式、文本槽位和图片槽位不被破坏。
