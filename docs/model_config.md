# 第 12 步模型配置

首版只接 OpenAI。以下数值来自官方文档，**真实接口尚未验证**：当前环境没有 `OPENAI_API_KEY`，本步未发送收费请求；1536 维是已核对的模型默认输出维度，不是本地实测结果。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无 | 仅创建真实客户端时必需，且只从进程环境读取。缺少时明确失败，不使用 fake 回退。 |
| `CHAT_MODEL` | `gpt-4.1-mini-2025-04-14` | 单独的聊天模型快照；通过 Chat Completions 的 `json_schema` 严格结构化输出。 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 单独的向量模型。 |
| `EMBEDDING_DIMENSIONS` | `1536` | 向量请求维度及返回校验维度；当前是该模型默认维度。变更模型或维度后，新旧向量不能混用，建索引前需确定迁移及重建策略。 |
| `MODEL_CONNECT_TIMEOUT_SECONDS` | `5` | HTTP 连接超时，单位秒。 |
| `MODEL_READ_TIMEOUT_SECONDS` | `30` | HTTP 读取超时，单位秒。 |
| `MODEL_MAX_ATTEMPTS` | `3` | 每次调用的总尝试次数，取值 1–3。 |

`embed_documents` 和 `embed_query` 使用相同的模型、维度及原文编码方式：UTF-8 字符串直接送入 API，不额外加 `query:` 或 `document:` 前缀。官方 OpenAI 接口没有为这个模型定义不同的 query/document 输入字段；若以后更换为需要任务前缀的模型，须同步重建文档向量。批量最多 2048 条，每条非空；官方单条上限 8192 **token**，单请求合计上限 300,000 token。当前不装 tokenizer，故不把切块的 600 字符误当 token 上限；超限时 API 的 400 错误直接上抛，不重试。[嵌入模型与维度](https://developers.openai.com/api/docs/guides/embeddings)、[嵌入请求限制](https://developers.openai.com/api/reference/resources/embeddings/methods/create)。

聊天模型官方标明 1,047,576 token 上下文、32,768 token 最大输出，支持结构化输出；本步不做本地 token 估算。调用方须传入符合 OpenAI 严格结构化输出子集的 JSON Schema，本层再用 `jsonschema` 检查返回对象。模型拒绝、截断、格式或 schema 不符都作为技术/模型错误交给上层处理。[模型规格](https://developers.openai.com/api/docs/models/gpt-4.1-mini)、[结构化输出指南](https://developers.openai.com/api/docs/guides/structured-outputs)。

HTTP 使用已锁定版本的 HTTPX，分别设置连接与读取超时。只对连接/读取的暂时性故障、HTTP 408/429/500/502/503/504 做指数退避，默认最多 3 次；认证失败、400/422 参数错误及返回向量异常只尝试一次。结果包含 token usage、耗时和本次调用次数，客户端还有累计调用次数。错误对象只保留安全错误码及尝试次数；不记录请求正文、完整文档、响应正文或密钥。[HTTPX 超时](https://www.python-httpx.org/advanced/timeouts/)、[HTTPX 异常分类](https://www.python-httpx.org/exceptions/)。

离线测试显式构造 `FakeEmbeddingClient` 和 `FakeChatClient`。fake 的 token usage 为 0，仅验证协议、控制流和错误边界，不代表真实 RAG 效果或真实模型可用性。
