# TaGeAI 调用服务器交接说明

本文档用于让新的 Codex 会话或 TaGeAI Gateway 快速接手服务器调用。文档只记录
部署拓扑、接口契约和非敏感模型信息，不记录任何 API Key、HMAC 密钥、公众号
AppSecret 或数据库密码。

## 1. 当前部署

- 服务器：`47.94.210.8`
- 项目目录：`/opt/wechat-ai-platform`
- 外部访问入口：`http://47.94.210.8:5173`
- 后端内部端口：`8002`，只绑定服务器本机 `127.0.0.1:8002`
- 对外 API 前缀：`/api/v1`
- 前端 Nginx 会把 `/api/` 反向代理到后端，因此 TaGeAI 不应直接访问 `8002`
- 服务器调度器：`celery-beat` 和 `celery-scheduled-worker`
- 本地 Windows Docker 和本地 Celery 已停止；不要为了验证服务器而启动本地调度器
- 不要修改或停止 Clash/Mihomo，它可能影响服务器运维连接和外部模型访问

## 2. 图片模型四层链路

当前服务器 `.env` 的图片链路顺序如下，上一层失败后进入下一层：

1. Kuai：`gpt-image-2`
2. hapiopen：`gpt-image-2`
3. 九野星霸：`https://api.jiuyeyingxiang.com/v1/xingba/image`，模型 `gpt-image-2`
4. 火山方舟官方：`doubao-seedream-4-5-251128`

第三层是异步接口：先 `POST /v1/xingba/image` 获取 `task_id`，再轮询
`GET /v1/xingba/image/{task_id}`，完成后下载图片并归档到 MinIO。TaGeAI 不需要
直接处理这些模型差异，只调用本项目的统一业务接口。

## 3. TaGeAI 入站 API

服务器项目已经注册以下路由：

```text
POST /api/v1/integrations/tageai/invocations
GET  /api/v1/integrations/tageai/invocations/{invocationId}
POST /api/v1/integrations/tageai/invocations/{invocationId}/cancel
POST /api/v1/integrations/tageai/callbacks
GET  /api/v1/integrations/tageai/connector-accounts
POST /api/v1/integrations/tageai/connector-accounts
POST /api/v1/integrations/tageai/connector-accounts/{accountRef}/disable
```

实际调用基地址应使用已配置的公网域名；没有域名时可暂用：

```text
http://47.94.210.8:5173
```

先检查：

```text
GET /api/v1/health
```

成功后再调用 TaGeAI 路由。不要直接请求 `http://47.94.210.8:8002`，该端口只对
服务器本机开放。

## 4. 认证方式

所有 TaGeAI 路由使用 HMAC-SHA256，不使用普通网页登录 JWT。服务器 `.env` 中的
`TAGEAI_INTEGRATION_CLIENTS` 保存客户端配置，至少包含：

```json
{
  "client_id": "由部署方提供",
  "signing_secret": "只放在双方服务端",
  "tenant_binding_id": "绑定标识",
  "tenant_id": 107,
  "target_account_bindings": {
    "TaGeAI 可见的公众号引用": "本项目内部公众号账号 ID"
  }
}
```

每次请求都必须带以下请求头：

```text
X-TageAI-Client-Id: <client_id>
X-TageAI-Timestamp: <Unix 秒时间戳>
X-TageAI-Nonce: <本次请求唯一随机值>
X-TageAI-Signature: sha256=<十六进制 HMAC>
Idempotency-Key: <创建调用时必填，建议使用 executionId 或稳定幂等键>
```

签名原文规则为：

```text
clientId:HTTP_METHOD:规范化路径:规范化查询串:timestamp:nonce:sha256(body)
```

然后使用 `signing_secret` 做 HMAC-SHA256，结果为小写十六进制字符串。时间戳允许
与服务器相差最多 5 分钟；Nonce 在 Redis 中防重放，同一个 Nonce 不能重复使用。
查询和取消请求没有 JSON body 时，`body` 按空字节计算 SHA-256。

## 5. 创建一次文章调用

请求路径：

```text
POST /api/v1/integrations/tageai/invocations
```

最小请求体示例：

```json
{
  "invocationId": "tage-demo-20260821-001",
  "tenantBindingId": "已登记的绑定标识",
  "operation": "generate",
  "deliveryMode": "DRAFT",
  "targetAccountRef": "TaGeAI 中配置的公众号引用",
  "ownerUserId": "用户标识",
  "executionId": "tage-exec-20260821-001",
  "input": {
    "topic": "文章主题",
    "styleNotes": "写作要求"
  }
}
```

服务器返回 `202 Accepted` 只表示已经受理，不代表文章已经生成或发布。TaGeAI
应保存 `invocationId`，随后轮询：

```text
GET /api/v1/integrations/tageai/invocations/{invocationId}
```

最终状态、阶段、进度、文章预览、草稿 ID、发布 ID 和错误信息都在查询响应中。
如果确实需要停止未完成调用：

```text
POST /api/v1/integrations/tageai/invocations/{invocationId}/cancel
```

## 6. 新 Codex 会话接手步骤

让新会话第一句话直接说明：

```text
请先阅读 D:\wqjproject\wechat-ai-platform\docs\TAGEAI_SERVER_HANDOFF.md，
然后只读检查服务器 47.94.210.8 上 /opt/wechat-ai-platform 的容器状态和
/api/v1/health；不要启动本地 Docker/Celery，不要修改 Clash，不要输出或索要任何密钥。
我需要让 TaGeAI 调用服务器上的 integrations/tageai 接口。
```

之后再告诉它具体业务目标，例如“创建一次 DRAFT 调用并轮询结果”，不要让它猜测
服务器是否部署完成、模型层级或认证算法。服务器密钥已经在服务器 `.env` 中配置，
新会话只需要通过 SSH 做状态检查；若要修改配置，应先展示变更范围并重新核验服务。

## 7. 安全边界

- 不把服务器 `.env` 下载到本地，也不把密钥写入 Git、交接文档或聊天消息。
- 不把 `signing_secret`、模型 API Key、公众号 AppSecret 放入请求体。
- 不用普通网页登录账号代替 TaGeAI HMAC 认证。
- 不把内部公众号账号 ID 直接由 TaGeAI 请求体决定；它必须由服务器端的
  `target_account_bindings` 映射得到。
- 创建请求必须使用稳定的 `invocationId` 和 `Idempotency-Key`，避免网络重试造成
  重复生成或重复发布。
