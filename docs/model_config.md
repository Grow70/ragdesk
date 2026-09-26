# 第 12 步模型配置

首版只接 OpenAI。以下数值来自官方文档，**真实接口尚未验证**：当前环境没有 `OPENAI_API_KEY`，本步未发送收费请求；1536 维是已核对的模型默认输出维度，不是本地实测结果。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无 | 仅创建真实客户端时必需，且只从进程环境读取。缺少时明确失败，不使用 fake 回退。 |
| `CHAT_MODEL` | `gpt-4.1-mini-2025-04-14` | 单独的聊天模型快照；通过 Chat Completions 的 `json_schema` 严格结构化输出。 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 单独的向量模型。 |
| `EMBEDDING_DIMENSIONS` | `1536` | 向量请求维度及返回校验维度；当前是该模型默认维度。第 13 步的数据库列固定为 `vector(1536)`，命令行入库拒绝其他维度；变更需先迁移并重建索引。 |
| `MODEL_CONNECT_TIMEOUT_SECONDS` | `5` | HTTP 连接超时，单位秒。 |
| `MODEL_READ_TIMEOUT_SECONDS` | `30` | HTTP 读取超时，单位秒。 |
| `MODEL_MAX_ATTEMPTS` | `3` | 每次调用的总尝试次数，取值 1–3。 |

`embed_documents` 和 `embed_query` 使用相同的模型、维度及原文编码方式：UTF-8 字符串直接送入 API，不额外加 `query:` 或 `document:` 前缀。官方 OpenAI 接口没有为这个模型定义不同的 query/document 输入字段；若以后更换为需要任务前缀的模型，须同步重建文档向量。批量最多 2048 条，每条非空；官方单条上限 8192 **token**，单请求合计上限 300,000 token。当前不装 tokenizer，故不把切块的 600 字符误当 token 上限；超限时 API 的 400 错误直接上抛，不重试。[嵌入模型与维度](https://developers.openai.com/api/docs/guides/embeddings)、[嵌入请求限制](https://developers.openai.com/api/reference/resources/embeddings/methods/create)。

聊天模型官方标明 1,047,576 token 上下文、32,768 token 最大输出，支持结构化输出；本步不做本地 token 估算。调用方须传入符合 OpenAI 严格结构化输出子集的 JSON Schema，本层再用 `jsonschema` 检查返回对象。模型拒绝、截断、格式或 schema 不符都作为技术/模型错误交给上层处理。[模型规格](https://developers.openai.com/api/docs/models/gpt-4.1-mini)、[结构化输出指南](https://developers.openai.com/api/docs/guides/structured-outputs)。

HTTP 使用已锁定版本的 HTTPX，分别设置连接与读取超时。只对连接/读取的暂时性故障、HTTP 408/429/500/502/503/504 做指数退避，默认最多 3 次；认证失败、400/422 参数错误及返回向量异常只尝试一次。结果包含 token usage、耗时和本次调用次数，客户端还有累计调用次数。错误对象只保留安全错误码及尝试次数；不记录请求正文、完整文档、响应正文或密钥。[HTTPX 超时](https://www.python-httpx.org/advanced/timeouts/)、[HTTPX 异常分类](https://www.python-httpx.org/exceptions/)。

离线测试显式构造 `FakeEmbeddingClient` 和 `FakeChatClient`。fake 的 token usage 为 0，仅验证协议、控制流和错误边界，不代表真实 RAG 效果或真实模型可用性。

第 13 步的入库配置 ID 由 `provider:model:dimensions:raw-v1` 构成；`raw-v1` 表示查询和文档均直接嵌入原文。构建另外保存提供方、模型名、维度、配置版本及字符切块参数。当前 fake 使用 `fake:sha256-onehot-v1:1536:raw-v1`，真实 OpenAI 默认使用 `openai:text-embedding-3-small:1536:raw-v1`。同一知识库的有效构建不得混用这两种 ID；后续查询也必须提供相同 ID 才能读取块。fake 的确定性 one-hot 向量只供测试流程。

第 14 步查询默认使用 `RETRIEVAL_EMBEDDING_BACKEND=openai`，以相同 `EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` 和 `raw-v1` 配置 ID 过滤构建。仅在离线 fake 索引上显式设置 `RETRIEVAL_EMBEDDING_BACKEND=fake`；缺失真实 API 密钥不会隐式切换。若某库没有该配置的有效片段，返回空结果，不发送模型请求。真实语义冒烟检查单独运行并记录，尚未执行前不得把手算向量测试称为语义质量验证。


第 15 步固定 RAG 使用现有 Chat 模型和单独的 Embedding 配置。`OpenAIChatClient` 新增 `max_completion_tokens` 构造参数，默认 1024；问答路由使用 `ContextBudget.output_tokens` 设置同值，模型输出截断仍是错误响应。预算公式为 `消息 JSON UTF-8 字节数 + schema UTF-8 字节数 + output_tokens + overhead <= total`，默认 `total=16384`、`output_tokens=1024`、`overhead=1024`。这是一种偏保守的应用限制，未安装 tokenizer，不是精确模型 token 数。系统提示和问题先占输入额度，候选按排名只加入放得下的完整块。配置通过服务的 `ContextBudget` 参数传入；HTTP 调用者不能提高预算。真实 Chat 和 RAG 效果本步未验证。

已核对官方 [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 的 `json_schema` 用法与非法/截断输出处理，以及 [输出 token 计量](https://developers.openai.com/api/docs/guides/token-counting) 中 `max_completion_tokens` 的限制范围。JSON Schema 只验证结构；本项目另外验证引用集合和当前来源权限，不把两者视为语义支持证明。
