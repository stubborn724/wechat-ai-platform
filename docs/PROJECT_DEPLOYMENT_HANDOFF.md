# 微信公众号 AI 平台部署与 TaGeAI 交接手册

> 文档版本：2026-08-22。本文档不包含 API Key、HMAC 密钥、公众号 AppSecret、数据库密码或 SSH 私钥。

## 1. 项目定位

本项目是微信公众号 AI 运营平台，负责文章生成、参考文章仿写、ERP 产品图生图、品牌格式控制、审核、素材归档、微信公众号草稿/群发和定时任务。网页端通过 Vue + FastAPI 使用，TaGeAI 通过独立的 HMAC 服务间接口调用。项目还连接 ERP、模型中转站、火山方舟、微信公众号接口/中转站、MySQL、PostgreSQL、Redis、MinIO 和 Celery。

## 2. 服务器与拓扑

- 服务器：`47.94.210.8`
- 项目目录：`/opt/wechat-ai-platform`
- 网页入口：`http://47.94.210.8:5173`
- 后端端口：`8002`，只绑定服务器本机 `127.0.0.1:8002`
- 对外 API 前缀：`/api/v1`
- Compose 文件：`deploy/docker-compose.server.yml`
- 生产调度只使用服务器上的 Celery，Windows 本地 Docker/Celery 已停止
- 不要关闭或修改 Clash/Mihomo，现有运维和外部模型访问可能依赖它

生产容器职责：`backend` 提供 FastAPI；`celery-worker` 执行普通队列；`celery-scheduled-worker` 执行定时队列；`celery-beat` 产生定时消息；MySQL 保存业务数据；PostgreSQL 保存知识库向量；Redis 保存队列、结果和 TaGeAI nonce；MinIO 保存图片、视频、文档和生成素材；frontend 由 Nginx 托管并把 `/api/` 转发给 backend。

SSH 连接方式（私钥只使用本机文件，不要复制私钥内容）：

```powershell
ssh -b 10.60.11.25 -p 22 -i C:\Users\25479\.ssh\id_ed25519_wechat_ai_deploy root@47.94.210.8
```

连接后的第一轮只读检查：

```bash
cd /opt/wechat-ai-platform
docker compose --env-file .env -f deploy/docker-compose.server.yml ps
df -h /
docker exec wechat-platform-redis redis-cli PING
```

不要打印服务器 `.env` 全文；配置检查只输出非敏感变量和模型名，密钥显示为 `<configured>`。

## 3. 已完成的功能

### 3.1 内容与仿写

- 标题生成、标题仿写、标题语义校验、大纲生成与修改、正文生成
- Markdown/HTML 图文合并、知识库检索、质量评估、审核和定向修改
- 抓取公众号/RSS 文章，分析标题、语气、段落、图片顺序、纯图片画廊和图文混排格式
- 通过格式模板和 DOM 槽位回填保护文章结构，避免模型直接破坏 HTML

### 3.2 ERP 图生图

- 从 ERP 选择产品主图和产品信息
- 按 ERP 来源键路由同品牌格式知识库和视觉背景知识库
- 优先传递 ERP 原图字节，减少公网 URL 和 DNS 问题
- 生成结果下载后归档到 MinIO，再用于文章和微信素材
- 同篇产品身份证：所有图片必须是同一件产品，不是同系列另一款
- 场景约束：床进卧室、餐桌进餐厅等，不能把产品放入错误空间
- 已知信息边界：只使用原图可见面、材质和结构，不补造背面、侧后、底部、内部或被遮挡结构

### 3.3 海报与品牌格式

- 海报标题、内容海报和末尾咨询卡按固定顺序生成
- 公众号标题保留产品名称或明确品类
- 联系方式、电话、二维码、Logo 和水印由程序/模板处理，模型禁止自行绘制
- 海报允许低对比度、低饱和、柔光、轻微薄雾、浅景深和边缘柔焦
- “朦胧”只表示空间氛围柔和，不能把产品变成空白光影或不可辨认家具
- 绣蔓、剪纸、写怀、中西无界使用各自知识库和格式模板；普通图文不继承海报零间距规则

### 3.4 微信与定时任务

- 支持多个公众号、草稿模式、直接发布、公域/私域发布域和文章同步
- 定时任务按日期、时间、发布域和账号生成 `scheduled_task_runs`
- 任务状态通常为 `queued`、`running`、`completed`、`failed`
- 当前同一任务互斥；不同任务受 `SCHEDULED_TASK_MAX_ACTIVE_RUNS=2` 限制
- 公众号投递并发为 `SCHEDULED_DRAFT_DELIVERY_MAX_WORKERS=2`
- 当前 5 个公众号：私域每天 `08:00`，公域每天 `13:00`、`20:00`
- 测试时间 `15:50` 已从 5 个公域任务移除，只删除时间点，不删除任务和历史记录

## 4. 当前生产模型与配置

### 4.1 文生文

服务器实际链路为：第一层 Kuai `gpt-5-mini`，第二层 Kuai secondary `qwen3.5-flash`。

```env
TEXT_GENERATION_PROVIDER_CHAIN=kuai,kuai_secondary
TEXT_GENERATION_MODEL=gpt-5-mini
TEXT_GENERATION_SECONDARY_MODEL=qwen3.5-flash
```

### 4.2 图片生成四层

1. Kuai OpenAI-compatible：`gpt-image-2`
2. hapiopen：`gpt-image-2`
3. 九野星霸：`gpt-image-2`，接口为 `POST /v1/xingba/image`
4. 火山方舟官方：`doubao-seedream-4-5-251128`

配置链为：

```env
IMAGE_GENERATION_PROVIDER_CHAIN=kuai_openai_compatible,kuai_seedream_40,jiuye_image_2,volcengine_ark
IMAGE_GENERATION_FALLBACK_ON_ANY_ERROR=true
IMAGE_GENERATION_JIUYE_MODEL=gpt-image-2
IMAGE_GENERATION_ARK_MODEL=doubao-seedream-4-5-251128
```

九野不是同步 OpenAI 接口，必须先 `POST /v1/xingba/image` 获取 `task_id`，再轮询 `GET /v1/xingba/image/{task_id}`，成功后下载结果并归档 MinIO。不能只替换模型字符串复用错误协议。图片上一层出现可捕获错误时继续下一层；全部失败才让文章失败。

### 4.3 视觉识别

ERP 产品命名/识别默认使用 Kuai 兼容视觉模型 `qwen3-vl-8b-instruct`。火山方舟视觉 Endpoint 必须先确认已开通并实际返回成功，404 模型不能进入生产兜底链。

### 4.4 密钥原则

所有真实密钥只放服务器 `.env`，不写 Git、不写交接文档、不写请求体、不打印日志。修改模型时必须同步检查 provider 名称、base URL、模型名、编辑模型名、超时和适配器协议。

## 5. TaGeAI 接口与使用方法

TaGeAI 调用的是本项目服务器上的业务接口，不是微信官方接口，也不是模型中转站接口。外部基地址：

```text
http://47.94.210.8:5173/api/v1/integrations/tageai
```

不要直接访问后端 `8002`，该端口只绑定服务器本机。先检查 `GET /api/v1/health`。

已提供路由：

```text
POST /api/v1/integrations/tageai/invocations
GET  /api/v1/integrations/tageai/invocations/{invocationId}
POST /api/v1/integrations/tageai/invocations/{invocationId}/cancel
POST /api/v1/integrations/tageai/callbacks
GET  /api/v1/integrations/tageai/connector-accounts
POST /api/v1/integrations/tageai/connector-accounts
POST /api/v1/integrations/tageai/connector-accounts/{accountRef}/disable
```

每次请求使用 HMAC-SHA256，必须带：

```text
X-TageAI-Client-Id: <client_id>
X-TageAI-Timestamp: <Unix 秒时间戳>
X-TageAI-Nonce: <每次唯一随机值>
X-TageAI-Signature: sha256=<小写十六进制 HMAC>
Idempotency-Key: <创建调用时必填>
```

签名原文是：

```text
clientId:HTTP_METHOD:规范化路径:规范化查询串:timestamp:nonce:sha256(body)
```

时间戳与服务器最多相差 5 分钟；Nonce 由 Redis 防重放。服务端从 client_id 映射租户、公众号和回调配置，不能信任请求体直接指定内部账号 ID。

创建任务示例：

```json
{
  "invocationId": "tage-demo-20260822-001",
  "tenantBindingId": "已登记绑定",
  "operation": "generate",
  "deliveryMode": "DRAFT",
  "targetAccountRef": "TaGeAI 中的公众号引用",
  "ownerUserId": "用户标识",
  "executionId": "tage-exec-20260822-001",
  "input": {"topic": "文章主题", "styleNotes": "写作要求"}
}
```

创建返回 `202 Accepted` 只代表已受理。TaGeAI 必须保存 `invocationId` 并轮询查询，最终以 `status`、`phase`、`result` 和 `error` 为准。网络重试必须复用同一 `invocationId` 和 `Idempotency-Key`，避免重复生成或发布。

## 6. 历史问题、根因与处理

### DNS Fake-IP

Clash/Mihomo 的 Fake-IP 网段 `198.18.x.x` 不是公网真实地址，曾导致图片域名下载失败。现在优先传 ERP 图片字节、校验外部 URL，必要时才临时转存 HTTPS。不要把 `198.18.x.x` 当成服务器真实 IP。

### 中转站 403、额度和降级

过去把所有 403 都停止降级，导致额度不足或临时异常没有机会走下一层。图片当前开启任意错误继续降级。微信公众号结果“不明确”则不能盲目重试，因为可能已经群发成功，必须先查状态防重复发布。

### 万相/Endpoint 404

404 通常是 Endpoint 未开通、模型 ID 或路径不匹配。未实际验证成功的模型不能写生产链；正式第四层目前是方舟 `doubao-seedream-4-5-251128`，万相不在正式链路。

### 产品幻觉和错误房间

模型曾生成不同款式产品、虚构背面或把床放客厅。现在通过同篇产品身份证、可见结构边界、正确房间规则、已知景别规则、质量检查和有限重试控制。修改这些规则优先看 `scheduled_product_scene_service.py`、`poster_article_service.py`、`scheduled_image_quality_service.py` 和品牌知识库脚本。

### 微信 45028

`45028 has no masssend quota` 是公众号群发额度不足，不是图片模型问题。应检查公域/私域限制、公众号认证和当日额度，不能靠图片兜底解决。

### 磁盘满导致 13 点任务消失

服务器曾因 Docker `overlay2`、重复构建镜像、构建缓存和其他项目数据卷累积而满盘。Redis 无法保存 RDB 后拒绝写操作，Celery Beat 无法把 13:00 任务写入队列，因此 8:00 可能成功而 13:00 没有运行记录。之后云盘已扩容到 100G，`/dev/vda3` 扩展到约 99.8G，Redis 快照恢复成功；悬空镜像记录已清理，业务卷未动。

## 7. 修改与部署方法

### 7.1 修改模型或环境变量

只编辑服务器 `/opt/wechat-ai-platform/.env` 中对应变量，不改 `.env.example` 代替生产配置。先验证新 Endpoint 和模型，再更新配置。后端和 Worker 读取启动时配置，通常只重建：

```bash
cd /opt/wechat-ai-platform
docker compose --env-file .env -f deploy/docker-compose.server.yml up -d --build --no-deps backend celery-worker celery-scheduled-worker
```

不要无理由重启 `celery-beat`、数据库、Redis、MinIO 或前端。修改调度代码才评估 Beat；任何可能重复投递的操作都要先确认。

### 7.2 修改图片层级

同步修改 `IMAGE_GENERATION_PROVIDER_CHAIN` 和对应 provider 配置，并确认 `image_generation_service.py` 的 factory 已注册 provider。测试顺序：运行时 chain、各层模型名、单 provider 请求、失败后下一层、结果归档 MinIO、服务状态。

### 7.3 修改提示词/品牌格式

品牌背景和格式优先改知识库或 `backend/scripts/rebuild_brand_split_knowledge_bases.py`；写作模板看 `writing_style_template_service.py`；产品身份和场景看 `scheduled_product_scene_service.py`；海报视觉看 `poster_article_service.py`；通用 Agent 看 `backend/app/constants/prompt.py` 与 `article_agent_service.py`。必须添加对应测试，确认绣蔓格式、普通图文和 ERP 产品身份不互相污染。

### 7.4 修改定时任务

优先使用网页或后端 API。先确认是改时间点还是删整个任务、公域还是私域、是否保留历史运行记录、时间是否已过会不会补跑。删除测试时间只移除 `publish_times` 数组项，不删除文章、素材或 `scheduled_task_runs`。

## 8. 排障手册

没有运行记录：检查服务器时间、任务启用状态、publish_times、Beat、Redis、磁盘和 Beat 日志。`queued` 不动：检查 scheduled worker、Redis 连接、并发槽和同一任务互斥。图片失败：按 provider/model/HTTP 状态/错误类别逐层检查，不要先改提示词。微信失败：区分 45028、401/403、网络错误和结果不明确；有 `publish_id` 或 `msg_data_id` 时不要重复发布。

只读磁盘检查：

```bash
df -h /
docker system df
du -sh /var/lib/docker/overlay2 /var/lib/docker/volumes /var/lib/docker/containers
```

可评估清理：未被容器引用的悬空镜像和 Docker 构建缓存。不可直接删除：微信公众号项目的 MinIO/MySQL/PostgreSQL/Redis 数据卷、其他项目的业务卷、运行中容器和正在写入的日志文件。日志应先配置轮转。

## 9. 当前状态与未来优化

截至 2026-08-22：四层图片链路已部署；TaGeAI HMAC 路由已实现；产品/场景/海报提示词规则已保留；5 个公众号为私域 08:00、公域 13:00/20:00；15:50 测试时间已移除；服务器磁盘扩容并恢复 Redis；悬空镜像已清理；业务数据卷未删除。

建议后续优化：

1. 配置 Docker 日志轮转和磁盘使用率告警
2. 为构建镜像设置保留策略，避免重复构建堆积
3. 监控 Redis、Beat、Worker、队列长度和“没有运行记录”的调度缺失
4. 为图片 provider 记录成功率、耗时、降级次数、错误类别和费用
5. 对微信结果不明确增加状态查询闭环，而不是直接重试
6. 增加模型 Endpoint 能力探测，阻止 404 模型进入生产链
7. 为 MinIO 区分 ERP 原图、生成图、文章引用图并制定生命周期策略
8. 为 TaGeAI 提供统一 SDK，减少各客户端重复实现 HMAC、幂等和轮询
9. 将生产配置变更纳入审计，但密钥继续通过服务器 Secret/环境变量注入

## 10. 新 Codex 会话接手指令

```text
请先阅读 D:\wqjproject\wechat-ai-platform\docs\PROJECT_DEPLOYMENT_HANDOFF.md，
再只读检查服务器 47.94.210.8 上 /opt/wechat-ai-platform 的容器、磁盘、Redis 和
/api/v1/health。不要启动本地 Docker/Celery，不要修改 Clash，不要输出或索要任何
API Key、HMAC 密钥、公众号 AppSecret、数据库密码或 .env 全文。修改生产环境前先
说明文件、服务、数据和调度影响，再执行并核验。
```

## 11. 变更验收清单

```text
[ ] 确认目标是服务器，不是本地环境
[ ] 没有启动第二个 Celery Beat
[ ] 没有修改 Clash/Mihomo
[ ] 没有把密钥写入文档、日志或 Git
[ ] 已确认模型 Endpoint、模型 ID 和计费
[ ] 已确认图片降级顺序和微信重试边界
[ ] 已确认定时任务时间、发布域和公众号账号
[ ] docker compose ps 正常
[ ] health、Redis、磁盘检查正常
[ ] scheduled_task_runs 和 delivery_results 已核对
```
