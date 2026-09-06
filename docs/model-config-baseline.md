# 模型配置基线与差异

## 接口基线

- OpenAI Models API：`GET /v1/models` 返回当前凭据可访问的模型目录，核心字段为 `id`、`created`、`owned_by`。原文：https://platform.openai.com/docs/api-reference/models/list
- 本模块按 OpenAI 兼容约定调用 `/models`、`/embeddings` 和 `/chat/completions`，可接入实现这些端点的云服务或本地服务。

## 本项目补充

`/models` 的标准目录对象没有声明某个模型是否能执行 embedding 或结构化 chat。本项目不根据模型名称猜测能力：目录成功只标记“目录可访问”，任务绑定后通过真实的 embedding 或 JSON chat 请求记录“实测通过/失败”。

连接按 `tenant_id` 隔离；远程 URL 要求 HTTPS，本地回环、私网和 `.local` 地址允许 HTTP。API key 使用 Fernet 加密落库，响应只返回 `api_key_configured`，HTTP 请求日志对密钥字段脱敏。TLR 执行把连接 ID、模型 ID、URL 和本地属性写入运行清单，不保存密钥。
