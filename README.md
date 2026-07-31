# WeChat AI Platform — 微信公众号 AI 运营平台

基于 AI 的微信公众号内容运营平台，支持 AI 自动生成文章、仿写、审核、发布、评论管理、自动回复的全流程管理。

## 核心功能

### AI 文章生成管线
- **标题生成**：AI 根据主题生成多个标题方案
- **大纲生成**：结构化文章大纲，支持 AI 辅助修改
- **正文生成**：流式生成完整文章，支持 Markdown + 配图
- **图片生成**：自动分析配图需求，通过 AI 生图/本地素材库获取图片
- **图文合并**：自动将图片嵌入文章对应位置

### 仿写引擎
- 抓取公众号/RSS 文章作为参考源
- AI 分析文章风格特征（语气、词汇难度、句式结构等）
- 可选取具体文章内容作为仿写参考，AI 严格模仿其写作风格

### 审核流程
- 人工审核台：逐篇审阅 AI 生成的文章
- 通过/退回操作，退回时可填写修改意见
- 支持定向重写

### 发布管理
- 多公众号绑定管理
- 发布计划：配置星期、时段、文章槽
- 直接发布或保存到微信草稿箱
- AI 文章发布后自动获取 msg_data_id

### 微信文章同步
- 从微信拉取公众号的草稿箱和已发布文章列表
- 文章索引保存在本地，正文按需实时拉取
- 多公众号切换管理

### 评论管理 & 自动回复
- 同步微信文章的留言到本地
- 自动回复评论（可配置回复内容）
- 评论用户自动私信（可配置私信内容，不重复发送）
- 按公众号独立配置自动规则

### 知识库
- 上传文档（PDF/DOCX/MD/TXT）构建知识库
- 自动分块 + 向量化（pgvector）
- AI 生成文章时可自动检索引用知识库内容

### 素材管理
- 图片/视频/文档上传存储（MinIO）
- 标签分类、预览、归档
- 可在文章生成时手动选择本地素材

## 技术栈

### 后端
- **框架**：Python FastAPI
- **AI 管线**：LangGraph + LangChain
- **LLM**：阿里云 DashScope（通义千问）
- **图片生成**：通义万相
- **数据库**：MySQL（业务）+ PostgreSQL / pgvector（向量检索）
- **缓存**：Redis
- **对象存储**：MinIO
- **任务队列**：Celery

### 前端
- **框架**：Vue 3 + TypeScript
- **UI 组件**：Element Plus
- **状态管理**：Pinia
- **构建工具**：Vite
- **SSE 流式通信**：实时接收 AI 生成进度

### 基础设施
- Docker Compose 一键部署基础设施
- 支持开发/生产环境配置

## 快速开始

### 前置要求
- Docker & Docker Compose
- Python 3.11+
- Node.js 18+

### 1. 启动基础设施（MySQL / Postgres / Redis / MinIO）

```bash
docker compose up -d mysql postgres redis minio
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入以下必填项：

| 变量 | 说明 | 必填 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API Key（通义千问） | 是（AI 生成文章需要） |
| `MYSQL_PORT` | MySQL 端口，Docker 映射的是 3307 | 否（默认 3306） |

其他数据库配置（MySQL / Postgres / Redis / MinIO）使用默认值即可，与 docker-compose.yml 一致。

### 3. 后端启动

```bash
cd backend
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac / Linux:
# source venv/bin/activate

pip install -r requirements.txt
python init_db.py         # 创建数据库表
python seed.py            # 创建管理员账号
python -m app.main        # 启动后端服务（端口 8000）
```

### 4. 前端启动

新开一个终端：

```bash
cd frontend
npm install
npm run dev               # 启动前端开发服务器（端口 5173）
```

### 5. 访问

打开浏览器访问 `http://localhost:5173`

- 默认管理员账号：`admin@wechat.ai`
- 默认密码：`admin123`

### 6. 配置 Celery（可选，用于定时任务）

如需定时同步文章、自动评论轮询等功能，启动 Celery：

```bash
docker compose up -d celery-worker celery-beat
```

## 公众号配置

### 添加公众号
1. 登录系统 → 公众号管理 → 添加公众号
2. 填入微信公众号的 **AppID** 和 **AppSecret**
3. 保存后即可在文章管理和评论管理中选择该公众号

### 微信 API 调用通道

面向普通用户时推荐使用固定 IP 中转站模式。用户只需要在系统里填写公众号
**AppID** 和 **AppSecret**，后端会调用中转站，由中转站服务器访问微信官方
API，因此普通用户不需要拥有微信后台管理员权限，也不需要自行配置 IP 白名单。

```env
WECHAT_API_CHANNEL=relay
WECHAT_RELAY_BASE_URL=http://8.166.141.59:21111
WECHAT_RELAY_APP_ID=relay_client
WECHAT_RELAY_SECRET=replace-with-relay-secret
```

`WECHAT_RELAY_SECRET` 是中转站 HMAC 密钥，必须由中转站维护方单独发放，不能
和微信公众号 `AppSecret` 混用，也不要写入日志。

如果是自管部署并且你能管理公众号后台，也可以使用 `WECHAT_API_CHANNEL=direct`。
direct 模式会由本机后端直连 `api.weixin.qq.com`，此时仍然需要在
`mp.weixin.qq.com` 的“开发 → 基本配置 → IP 白名单”中配置后端服务器出口 IP。

当前中转站文档已覆盖文章草稿/发布接口；文章同步、评论、客服消息、阅读数据等
能力在 `relay` 模式下不会再直连微信官方 API，需要中转站继续提供对应接口后启用。

### 接口权限
| 功能 | 所需公众号类型 |
|------|---------------|
| AI 生成文章、发布 | 订阅号 / 服务号均可 |
| 同步草稿箱、已发布文章列表 | 认证服务号 |
| 评论管理、自动回复 | 认证服务号 |
| 客服消息（自动私信） | 认证服务号 |

## 项目结构

```
backend/
├── app/
│   ├── agent/            # LangGraph AI 管线
│   │   ├── nodes/        # 各 Agent 节点
│   │   ├── tools/        # LangChain 工具
│   │   └── graph.py      # 管线编排
│   ├── api/v1/           # RESTful API 路由
│   ├── models/           # SQLAlchemy 模型
│   ├── schemas/          # Pydantic 数据模型
│   ├── services/         # 业务逻辑层
│   ├── tasks/            # Celery 定时任务
│   └── constants/        # Prompt 模板
├── docker-compose.yml
├── init_db.py
└── seed.py

frontend/
├── src/
│   ├── api/              # API 客户端
│   ├── views/            # 页面组件
│   ├── stores/           # Pinia 状态
│   ├── router/           # 路由配置
│   └── utils/            # 工具函数（SSE 等）
└── package.json
```
