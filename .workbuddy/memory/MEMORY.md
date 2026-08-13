# 项目长期记忆 — wechat-ai-platform

## 关键事实
- 本地部署：FastAPI 端口 **8002**（`backend/app/config.py`，README 的 8000 已过时），
  Vite 前端端口 **5173**，基础设施 MySQL/Postgres/Redis/MinIO/Celery 走 Docker Compose。
- 平台恢复脚本：`scripts/start-local-platform.ps1`（幂等，自动拉起 Docker Desktop）；
  自启动注册脚本：`scripts/register-local-platform-startup-task.ps1`。
- 公众号 API 通道：默认 `WECHAT_API_CHANNEL=relay` 中转站模式（普通用户无需 IP 白名单）；
  自管部署可用 `direct`。
- API 全部挂载在 `/api/v1` 前缀下；完整端点目录见 skill 的 `references/api_reference.md`。

## Skill 约定
- 项目级 skill：`.workbuddy/skills/wechat-ai-content-operations/`（操作平台 REST API）。
- 操守规则：认证用 `POST /auth/login`；默认 `publish_mode=draft`；绝不暴露
  AppSecret/ERP client_secret/API key；改定时任务前先读；不重复入队运行中的任务。

## 已修复的坑（勿回退）
- **GBK/emoji 打印崩溃**：后端大量 `print` 带 emoji，Windows 中文系统重定向 stdout
  到文件时 GBK 编码抛 UnicodeEncodeError → 文章创建接口 500、记录卡 pending。
  修复点：`backend/app/main.py` 顶部强制 stdout/stderr UTF-8；
  `scripts/start-local-platform.ps1` 设 PYTHONIOENCODING/PYTHONUTF8。
  重启 API 用 `scripts/start-local-platform.ps1`（venv + 日志重定向到 logs/runtime/）。
- 登录账号：默认管理员 `admin@wechat.ai` / `admin123`（README）。
- 平台公众号：6 个已绑定，「我的家具号(165)」唯一带发布能力且健康（relay 通道 DRAFT/PUBLISH）。
