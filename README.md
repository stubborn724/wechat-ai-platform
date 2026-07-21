# WeChat AI Platform — 微信公众号 AI 运营平台

基于 AI 的微信公众号内容运营平台，支持 AI 自动生成文章、仿写、人工审核、定时发布的全流程管理。

## 核心功能

### 🤖 AI 文章生成管线
- **标题生成**：AI 根据主题生成多个标题方案，用户可手动选择或全自动
- **大纲生成**：结构化文章大纲，支持 AI 辅助修改
- **正文生成**：流式生成完整文章，支持 Markdown + 配图
- **图片生成**：自动分析配图需求，从 Pexels/本地素材库获取图片
- **图文合并**：自动将图片嵌入文章对应位置

### 📝 仿写引擎
- 抓取公众号/RSS 文章作为参考源
- AI 分析文章风格特征（语气、词汇难度、句式结构等）
- 可选取具体文章内容作为仿写参考，AI 严格模仿其写作风格和段落格式

### 👥 审核流程
- 人工审核台：逐篇审阅 AI 生成的文章
- 通过/退回操作，退回时可填写修改意见
- 支持定向重写

### 📅 发布管理
- 多公众号绑定管理
- 发布计划：配置星期、时段、文章槽
- 自动保存到微信草稿箱
- 微信开放平台扫码授权

### 🧠 知识库
- 上传文档（PDF/DOCX/MD/TXT）构建知识库
- 自动分块 + 向量化（pgvector）
- AI 生成文章时可自动检索引用知识库内容

### 📂 素材管理
- 图片/视频/文档上传存储（MinIO）
- 标签分类、预览、归档
- 可在文章生成时手动选择本地素材

### 📡 投喂源
- 支持 RSS/URL/公众号文章抓取
- 风格分析：提取文章风格特征用于仿写
- 手动添加文章

## 技术栈

### 后端
- **框架**：Python FastAPI
- **AI 管线**：LangGraph + LangChain
- **LLM**：阿里云 DashScope（通义千问）
- **图片生成**：通义万相 / Pexels API
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
- Docker Compose 一键部署
- 支持开发/生产环境配置

## 快速开始

### 前置要求
- Docker & Docker Compose
- Python 3.11+
- Node.js 18+

### 1. 启动基础设施

```bash
docker-compose up -d mysql postgres redis minio
```

### 2. 后端配置

```bash
cd backend
cp ../.env.example ../.env
# 编辑 .env 填入你的 API Key
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python init_db.py         # 初始化数据库表
python seed.py            # 插入初始数据（管理员账号）
python -m app.main        # 启动服务
```

### 3. 前端配置

```bash
cd frontend
npm install
npm run dev
```

### 4. 访问

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

## 环境变量

核心配置项（详见 `.env.example`）：

| 变量 | 说明 |
|------|------|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API Key（通义千问） |
| `PEXELS_API_KEY` | Pexels 图片搜索 API Key |
| `WECHAT_COMPONENT_*` | 微信开放平台第三方平台配置 |
| `MINIO_*` | MinIO 对象存储配置 |

## 项目结构

```
backend/
├── app/
│   ├── agent/          # LangGraph AI 管线
│   │   ├── nodes/      # 各 Agent 节点
│   │   ├── tools/      # LangChain 工具
│   │   └── graph.py    # 管线编排
│   ├── api/v1/         # RESTful API 路由
│   ├── models/         # SQLAlchemy 模型
│   ├── schemas/        # Pydantic 数据模型
│   ├── services/       # 业务逻辑层
│   └── constants/      # Prompt 模板
├── docker-compose.yml
└── init_db.py

frontend/
├── src/
│   ├── api/            # API 客户端
│   ├── views/          # 页面组件
│   ├── stores/         # Pinia 状态
│   ├── router/         # 路由配置
│   └── utils/          # 工具函数（SSE 等）
└── package.json
```

## License

MIT
