# 企业知识库问答系统架构契约（第 2 步）

本文以 [需求文档](requirements.md) 为依据，约定模块边界、数据契约、接口形状和一致性规则。后续每个编号任务以本文为契约；需要改变字段、状态、权限或接口时，先更新本文再实现。本步只写设计，不创建业务代码或安装依赖。

## 1. 系统边界与选型

- 前端：React + TypeScript，负责选库、上传与状态展示、检索、问答和引用跳转；前端显示的权限不是安全边界。
- API：FastAPI，负责 HTTP 参数校验、可信用户识别、授权入口、响应与错误映射、`request_id`。路由不直接解析文件、访问模型或拼接检索 SQL。
- 业务与持久化：Python services 编排流程；repositories 使用 SQLAlchemy 访问 PostgreSQL；Alembic 管理数据库模式迁移。
- 检索：PostgreSQL 存储文档、构建与片段；模型适配步骤确定嵌入维度后，再通过迁移加入 pgvector 向量列。基础范围将采用单库向量检索，混合检索留给 F1。
- 模型：第 12 步固定首个供应商为 OpenAI；`llm` 适配层分别配置嵌入和生成模型，密钥只从环境变量读取。模型标识、维度和输入约定见 [模型配置](model_config.md)。
- Agent：后续 F3 再使用 LangGraph 编排受限工具调用；基础问答为固定 RAG 流程，不启用 Agent。
- 文件：原始上传文件放在非公开的受控存储中，数据库只保存元数据及内部存储定位；不得提交到 Git。具体本地存储或对象存储方案待实现任务确定。

第 2 步编写本文时尚未安装依赖；第 3 步已在 `backend/uv.lock` 锁定后端依赖。后续引入模型适配等新组件时仍须核对兼容版本并更新锁文件。[FastAPI 多文件应用](https://fastapi.tiangolo.com/tutorial/bigger-applications/)、[SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)、[Alembic 文档](https://alembic.sqlalchemy.org/en/latest/)、[pgvector 官方说明](https://github.com/pgvector/pgvector)、[React TypeScript 指南](https://react.dev/learn/typescript) 和 [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview) 是本设计核对的官方资料。

## 2. 文档入库流程

基础范围可在请求内执行入库，上传请求完成后返回最终状态；未来 F2 将同一 service 流程交给后台任务执行，并可先返回 `202` 和 `task_id`。无论执行方式如何，构建状态和发布规则相同。

```mermaid
flowchart TD
    A[管理员上传并指定一个知识库] --> B[API 识别用户并校验管理员权限]
    B --> C[Service 校验格式与文件字节摘要]
    C --> D{同库存在未删除的相同文件?}
    D -- 是 --> E[返回已有 document_id 不创建构建]
    D -- 否 --> F[创建文档和 build 记录]
    F --> G[Parser 提取 ParsedSection]
    G --> N{文件可解析且有可提取文字?}
    N -- 是 --> H[切分 Chunk 并生成嵌入]
    H --> I[写入带 build_id 的候选片段]
    I --> J{全部片段完整且通过校验?}
    J -- 是 --> K[单个数据库事务发布 active_build_id]
    K --> L[状态可检索]
    J -- 否 --> M[标记 build 失败并记录原因]
    N -- 否 --> M
```

上传前校验若发现不支持类型，可直接返回明确的 4xx 错误且不建文档；接受后才发现损坏或扫描件时，build 进入失败态并保存可读原因。两种情况都不得产生可检索内容。重复判定基于同一知识库中未删除文档的原始文件字节摘要；数据库唯一约束处理并发重复上传，返回已有 `document_id`。摘要相同后仍以字节相同为契约，极端碰撞处理方式在实现任务确定。

## 3. 问答流程

```mermaid
flowchart TD
    A[成员选择一个知识库并提问] --> B[API 识别用户并校验该库成员权限]
    B --> C[Service 校验问题与单库选择]
    C --> D{问题需澄清?}
    D -- 是 --> E[AnswerResult needs_clarification]
    D -- 否 --> F[生成查询向量]
    F --> G[Retrieval 仅查询当前库有效 build 的片段]
    G --> H{证据足够?}
    H -- 否 --> I[AnswerResult insufficient_evidence]
    H -- 是 --> J[LLM 基于证据生成草稿]
    J --> K[Service 校验引用与当前权限及文档有效性]
    K --> L{答案和引用有效?}
    L -- 是 --> M[AnswerResult answered]
    L -- 证据不支持草稿 --> I
    F --> T[模型或数据库技术故障]
    G --> T
    J --> T
    T --> U[HTTP 错误响应 含 request_id]
```

检索查询必须在数据库层同时限定 `knowledge_base_id`、未删除文档及 `document.active_build_id = chunk.build_id`。获取片段后，service 只把当前库授权证据传给模型；返回前复核引用仍指向有效文档。明确无证据或草稿无证据支持时返回 `insufficient_evidence`；问题缺少必要限定条件时返回 `needs_clarification`。模型超时、嵌入失败、数据库不可用或校验器自身故障使用错误响应，不能伪装为资料不足。基础范围的“证据足够”判定规则和阈值待实验确定，不能仅凭模型自称有引用就通过。

## 4. 分层职责与依赖方向

依赖方向为 `api → services → repositories / parsers / retrieval / llm`；后续 `agent → services / retrieval / llm`，但 Agent 的工具仍走同一授权和数据访问规则。`contracts` 定义跨层的数据结构，不依赖框架对象。

| 层 | 职责 | 不负责 |
| --- | --- | --- |
| `api` | FastAPI 路由、请求/响应模型、认证依赖、输入校验、HTTP 状态码和 `request_id`；把可信 principal 与单个库 ID 交给 service。 | 文件解析、检索排序、事务编排、直接调用模型。 |
| `services` | 用例编排和授权决策；处理上传去重、构建发布、删除、检索问答、证据与引用校验；明确事务边界。 | HTTP 框架细节、数据库 SQL、供应商 SDK 细节。 |
| `repositories` | 用 SQLAlchemy 执行限定知识库范围的读写、唯一约束及事务；Alembic 维护表结构。 | 决定用户权限或生成回答。 |
| `parsers` | 解析 UTF-8 Markdown/TXT 与文本型 PDF，输出位置可追踪的 `ParsedSection`；识别扫描、损坏和不支持类型。 | 决定授权、调用模型、发布索引。 |
| `retrieval` | 依据单库与有效 build 过滤候选片段，排序并输出 `RetrievedChunk`；后续扩展混合检索。 | 跨库搜索、绕过授权、生成自然语言答案。 |
| `llm` | 封装嵌入和生成模型 API、超时及错误分类；支持可控 fake 实现供单元测试使用。 | 决定哪些用户可见、保存数据库记录。 |
| `agent` | 后续用 LangGraph 实现有限步状态流转、工具白名单和调用计数；复用 service 的授权检查。 | 绕过固定权限边界或在基础阶段接管普通问答。 |

`api` 不接受请求体中的 `user_id` 作为授权依据；即使客户端传入也必须拒绝或忽略。所有入口使用后端从可信认证凭据解析出的用户身份。`knowledge_base_id` 是资源选择参数，不是授权凭据。每个文档、任务、来源查询都要同时校验所属库和用户在该库的角色，防止直接猜测 ID 越权。

## 5. 数据契约与定位

下列字段名采用 JSON 风格 `snake_case`；Python 内部对象也沿用相同语义。`id` 字段是稳定的不透明标识，示例用字符串；具体 UUID 或其他生成方式在实现任务确定。页码从 1 开始；`heading_path` 为从上到下的标题数组；TXT 可使用行号。可空字段在 JSON 中用 `null`。

| 对象 | 必需字段与类型 | 可空字段与约束 |
| --- | --- | --- |
| `ParsedSection` | `section_index: int`, `text: str`, `source_locator: str`, `block_type: "paragraph" \| "list" \| "code"` | `document_id: str?`, `page_number: int?`, `heading_path: list[str]?`, `start_line: int?`, `end_line: int?`；必须至少有页码、标题路径或行号之一。解析器只接收受控文件路径，可选 `document_id` 由调用方传入，独立预览时为 `null`；`section_index` 从 0 开始。Markdown/TXT 使用原文行号范围，`page_number` 为 `null`；PDF 使用从 1 开始的物理页码作为 `page_number` 和 `source_locator`，行号为 `null`，每个有文字的页面至少独立成一个 section。 |
| `Chunk` | `chunk_id: str`, `document_id: str`, `build_id: str`, `knowledge_base_id: str`, `ordinal: int`, `text: str` | 同上四个定位字段；继承原文位置，不能跨文档或构建拼接。数据库 `chunks` 表通过 `build_id` 关联文档和知识库，读取时派生 `document_id`、`knowledge_base_id`；向量列在模型适配步骤增加，不暴露给 API。 |
| `RetrievedChunk` | `chunk_id: str`, `document_id: str`, `build_id: str`, `knowledge_base_id: str`, `text: str`, `score: float` | 同上定位字段；`score` 是所用检索器的排序值，不承诺跨算法可比。仅可来自当前库的有效 build。 |
| `Citation` | `citation_id: str`, `document_id: str`, `build_id: str`, `chunk_id: str`, `document_name: str`, `snippet: str`, `source_path: str` | 同上定位字段；`source_path` 指向需重新授权的来源接口，不能是公开文件地址。 |
| `AnswerResult` | `status: "answered" \| "insufficient_evidence" \| "needs_clarification"`, `answer: str`, `citations: list[Citation]`, `request_id: str` | `answered` 必须有非空、经校验的引用；其余两种状态的 `citations` 为空，`answer` 分别写明资料不足或需要补充什么。 |

文档定位的最小稳定组合为 `document_id + build_id + chunk_id + 页码或标题路径/行号`。引用带 `build_id`，因此重建索引后不会意外指向另一版片段；来源接口每次重新检查文档未删除、用户有权访问，且只返回该引用对应的资料。若旧构建已清理，返回来源不可用错误，不能映射到当前构建的同序号片段。

第 9 步仅解析 UTF-8 或带 UTF-8 BOM 的 Markdown/TXT。解析输出保留标题层级、列表标记、代码围栏及原文行号；标题本身作为 `heading_path`，不单独成为正文片段。普通块清理首尾空白与空行，代码围栏内部保持原样。解析器不执行代码、不请求链接；缺文件、错误编码、空正文和未闭合代码围栏返回可识别的解析错误。此步不写入 `chunks` 或调用嵌入模型。

第 10 步的 `parse_pdf(path, document_id?)` 使用 pypdf，返回 `PdfParseResult(sections: list[ParsedSection], warnings: list[PdfPageWarning], status: "complete" | "partial")`。`PdfPageWarning` 包含 `page_number`、`source_locator`、`code`、`message`；每个没有可提取文字的物理页均有警告：无图片页为 `PDF_NO_TEXT_PAGE`，检测到图片的页为 `PDF_IMAGE_ONLY_PAGE`（疑似扫描页，不能仅凭图片断言来源）。有文字与无文字页面混合时只返回有文字页面的 sections，状态为 `partial`；后续构建服务不得把 `partial` 当作完整成功发布为可检索构建，须报告警告并按不完整构建处理。整份无可提取文字、加密或损坏时抛出稳定错误码，不返回“完整成功”。文本清理只去除无意义的行首尾空白，不改写金额、日期或编号。当前不支持 OCR、复杂表格结构恢复或复杂双栏排版；带隐藏 OCR 文字层的扫描件及图片中的关键信息无法由 pypdf 保证识别。

第 11 步的纯函数 `chunk_sections(sections, config)` 返回 `ChunkingResult(chunks: list[ChunkDraft], stats: ChunkStats)`；`ChunkConfig` 默认 `chunk_size=600`、`overlap=80`，单位是 Python 字符串的 Unicode 码点数，不是模型 token，要求整数且 `chunk_size > 0`、`0 <= overlap < chunk_size`。草稿含文档 ID（可空）、从 0 开始的 `ordinal`、`text`、内容 SHA-256、标题路径、物理页码、起止行号及 `source_spans`。每个 `ChunkSourceSpan` 保存原 `section_index`、`source_locator`、在该 section 文本中的 `[char_start, char_end)` 字符范围及可用的精确行号/页码；草稿不生成 `chunk_id`、`build_id` 或向量。按同一文档、标题路径和页码组合短段落；代码块独立；拆分优先段落、句子，再按字符硬切。PDF 不跨物理页，overlap 只在同一组合内拆分长文本时使用，短文本只产一个草稿。`ChunkStats` 至少记录块数、输入/输出字符数、最短/最长/平均长度、短块数、超限块数、完全重复块数；短块阈值为 `chunk_size` 的一半。完全重复按块正文的 SHA-256 统计，不代表语义重复。后续持久化时再为草稿分配数据库 ID 并核对构建归属。

第 12 步约定 `EmbeddingClient.embed_documents(texts)` 与 `embed_query(text)` 均返回 `EmbeddingResult(vectors, usage, elapsed_ms, call_count)`；查询结果只有一个向量。`ChatClient.generate(messages, response_schema)` 返回 `ChatResult(content, usage, elapsed_ms, call_count)`，其中 `content` 是按 JSON Schema 校验的对象。`usage` 分别记录输入、输出和总 token；`call_count` 包含本次重试尝试，客户端累计调用次数另行可读。参数或响应错误、鉴权失败和向量校验失败不得重试或变成资料不足；仅临时网络、超时、429 和指定 5xx 做最多三次尝试。fake 客户端需由测试显式构造，真实客户端缺密钥或失败时不能回退 fake。本步只实现适配层，不连接检索、问答或构建发布。

第 13 步的本地 `ingest_document` 命令只处理已上传的一份私有文件，由持有数据库连接凭据的本地操作人显式调用，不对外提供 HTTP 入口。命令创建 `processing` 构建并记录解析版本、切块配置、Embedding 提供方/模型/维度及编码约定版本；退出事务后验证原文件摘要、解析、切块并按批调用 Embedding。每批在短事务中写候选块与向量；全部成功后以知识库行锁串行化发布，核对连续序号、预期总数、向量非空、文档未删除及同库其他有效构建的 Embedding 配置一致，再在短事务中标记 `ready` 并更新 `active_build_id`。失败构建标记 `failed`，旧 active build 不变。重复执行同一 `build_id` 不追加块或重复调用模型；失败构建需要新的 ID 才能重建。查询必须带完整的 Embedding 配置标识，且只读取匹配配置、非空向量的有效构建；不同配置不能混入同一有效知识库索引。PDF 部分解析不发布。本步不提供相似度排序或问答。

## 6. 数据模型、状态与发布规则

第 4 步的核心表为 `users`、`knowledge_bases`、`kb_members(user_id, kb_id, role)`、`documents(id, kb_id, file_name, file_sha256, deleted_at, active_build_id)`、`document_builds(id, document_id, status, parser_config, chunking_config, model_config_id, error_code, error_message, created_at, finished_at)` 和 `chunks(id, build_id, ordinal, body, content_sha256, page_number, heading_path, start_line, end_line)`；文档的私有文件存储定位也保存在 `documents.storage_key`。`chunks` 的文档及知识库归属由 build 和 document 外键链确定，避免重复列失配。`kb_members` 对用户与知识库组合唯一；未删除文档的同库文件摘要唯一；`active_build_id` 必须引用本文件的构建。第 13 步通过迁移增加 `chunks.embedding vector(1536)` 与原文 span JSON，并为构建增加 Embedding 提供方、模型、维度、配置版本和预期块数；旧行允许空值，但查询排除空向量。F2 时增加 `tasks`。

核心表采用 Alembic 显式迁移，应用启动不调用 `create_all`。`active_build_id` 使用 `(documents.id, active_build_id)` 到 `(document_builds.document_id, id)` 的组合外键，防止指向其他文档的构建；可检索块查询还需检查当前库、未删除和 build 状态为 `ready`。第 5 步为 `users` 增加可空的 `login_name` 与 `password_hash`，使第 4 步已有的无登录资料用户仍可保留；仅演示初始化命令创建带 Argon2id 哈希的可登录用户。表定义与迁移用法参考 [SQLAlchemy 声明式映射](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html)、[PostgreSQL 方言](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html) 和 [Alembic 迁移教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。

构建状态：`queued → processing → ready` 或 `queued/processing → failed`。`ready` 表示该 build 的全部解析、分块、向量及索引记录已经完成并校验；只有被 `documents.active_build_id` 指向的 ready build 才对检索生效。`failed` 必须有机器可识别的错误码和供管理员查看的安全说明。文档对外状态至少有 `uploaded`、`processing`、`ready`、`failed`、`deleted`；第 8 步只保存原文件且不创建 build，因此新文档为 `uploaded`、`active_build_id` 为空且不可检索。若重建失败但旧 active build 仍在，文档保持 `ready`，同时展示最新构建 `failed` 及原因。F2 的任务状态可映射到构建状态，但任务不是引用来源。

发布时在同一个 PostgreSQL 事务内核对构建仍属该文档、状态为 ready、片段完整、文档未删除；引入向量列后还须核对向量齐全，再切换 `active_build_id`。首次构建未完整成功前指针为空；重建期间旧指针继续服务，失败时指针不变。检索和来源查询仅查询有效指针；候选写入再多也不可见。删除在事务中设置 `deleted_at` 并清空指针，删除成功后新请求立即不可见；物理文件及旧片段的清理可稍后执行，但来源接口也必须检查删除标记。发布与删除竞争时用文档行锁或等效条件更新串行化，不能让已删除文档重新发布。

## 7. 授权与错误规则

- 认证：第 5 步提供 `POST /auth/session` 和 `GET /auth/me`。演示用户由显式命令创建，不设默认密码；密码只以 Argon2id 哈希保存。登录成功签发有 `sub`（用户 UUID）、`iat` 和 `exp` 的 Bearer JWT；签名算法固定为 HS256，服务端仅从环境变量读取签名密钥。后端校验签名、算法、必需声明、过期时间及用户仍存在后，才把 `sub` 作为 principal。缺失或无效令牌返回 `401`；错误密码与不存在账号返回相同响应。不得读取请求体 `user_id` 决定身份。第 5 步不实现知识库角色授权，也不提供注册、找回密码、短信登录、刷新令牌或 SSO。用法参考 [FastAPI JWT 教程](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、[PyJWT 过期声明](https://pyjwt.readthedocs.io/en/stable/usage.html) 和 [argon2-cffi API](https://argon2-cffi.readthedocs.io/en/stable/api.html)。
- 知识库权限：管理员可上传、查看处理状态、删除、管理成员，并具有成员能力；成员可检索、问答、查看授权来源；未加入的用户不可访问。未知库或无成员资格建议统一返回 `404`，避免泄露库是否存在；已授权成员尝试管理员操作返回 `403`。
- 第 6 步的知识库接口仅使用已验证令牌中的用户 ID。创建知识库与创建者管理员成员在同一事务提交；列表只查询当前成员资格。`require_kb_member(session, user_id, kb_id)` 每次从数据库检查成员资格，未知库与非成员均返回同一 `404`；`require_kb_admin` 在前者基础上拒绝普通成员的管理操作。管理员可将已有用户加入或调整为 `member`/`admin`，也可移除成员；移除或降级最后一名管理员返回 `409`。成员写入在知识库行锁保护下完成，避免并发操作令管理员数归零。后续文档、检索、引用和任务接口必须在访问资源前复用这两个守卫，并在查询中限定库 ID。锁行为参考 [SQLAlchemy `with_for_update`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update) 和 [PostgreSQL 行锁](https://www.postgresql.org/docs/17/explicit-locking.html)。
- 所有列表、详情、任务、引用、检索 SQL 都带知识库约束；先按可信 principal 检查 membership，再按同库资源 ID 查询。前端隐藏按钮仅改善体验，不能代替后端检查。
- 参数或格式错误返回 `400/415/422` 中合适状态，错误体说明原因；冲突依照接口语义返回 `409`。模型、存储或数据库故障返回 `5xx` 与可追踪错误码。内部堆栈和密钥不能返回客户端。
- 每次请求生成或透传受控的 `request_id`，成功响应和错误响应均包含它；日志记录关联 ID，避免记录密钥和完整私有文档。

技术故障错误体统一为 `{ "error": { "code": "...", "message": "..." }, "request_id": "..." }`，不用 `AnswerResult.status` 表达故障。`insufficient_evidence` 只表示业务上没有足够的授权证据。

后续实现权限、检索、一致性及错误处理前，至少按下列契约检查编写有价值的测试；真实模型效果另行测量：

| 边界 | 可执行检查 |
| --- | --- |
| 可信身份 | 请求体伪造其他 `user_id`，仍按后端 principal 判权；未授权库的搜索、回答、来源和任务均不泄露内容。 |
| 单库检索 | A、B 库有各自独有事实时，A 库查询只返回 A 的有效 build；省略或传入多个库被拒绝。 |
| 构建发布 | 构建仅写入部分片段或向量失败时，`active_build_id` 不改变，新片段不可检索；完整成功后一次切换。 |
| 发布与删除竞争 | 删除成功后即使旧构建稍后结束，也不能恢复有效指针；新检索与旧引用均无法读取已删除内容。 |
| 回答与引用 | fake 模型分别返回有效引用、无证据、需澄清和编造引用，检查三个业务状态及引用校验。 |
| 技术故障 | fake 模型超时及数据库异常分别产生带 `request_id` 的错误响应，而非 `insufficient_evidence`。 |

## 8. HTTP 接口规划

以下为目标接口形状，不表示已实现。路径版本前缀为 `/api/v1`。除认证入口外均需认证；`{kb_id}` 在路径中指定且仅有一个。接口与阶段按需求 R1–R9、F2 对应。创建知识库时，创建者成为该库管理员；演示账号的建立方式待认证方案确定。

| 分类 | 方法与路径 | 权限 | 主要请求 / 响应 |
| --- | --- | --- | --- |
| 认证 | `POST /auth/session`, `GET /auth/me` | 登录入口 / 已认证 | `POST` 接收 `{ "login_name": "...", "password": "..." }`，返回 Bearer `access_token`、`expires_in` 和 `request_id`；`me` 返回从已验证令牌识别的用户 ID、登录名、显示名和 `request_id`。第 5 步不提供注销或令牌撤销接口。 |
| 知识库 | `GET /knowledge-bases`, `POST /knowledge-bases`, `GET /knowledge-bases/{kb_id}` | 已认证；详情需成员 | 仅列出有权访问的库；创建者为管理员。 |
| 成员授权 | `GET /knowledge-bases/{kb_id}/members`, `PUT /knowledge-bases/{kb_id}/members/{user_id}`, `DELETE /knowledge-bases/{kb_id}/members/{user_id}` | 管理员 | 查看、授予或调整该库成员角色，或移除成员；`PUT` 请求 `{ "role": "member" | "admin" }`；不允许移除或降级最后一名管理员。未知库或非成员统一 `404`。 |
| 文档 | `POST /knowledge-bases/{kb_id}/documents`, `GET /knowledge-bases/{kb_id}/documents`, `GET /knowledge-bases/{kb_id}/documents/{document_id}`, `GET /knowledge-bases/{kb_id}/documents/{document_id}/raw`, `DELETE /knowledge-bases/{kb_id}/documents/{document_id}` | 上传、删除限管理员；读取需成员 | 上传、分页列表与状态、详情、受保护的原文件下载；重复上传返回已有 ID。删除留待后续步骤。 |
| 来源 | `GET /knowledge-bases/{kb_id}/sources/{document_id}/{build_id}/{chunk_id}` | 成员或管理员 | 返回授权片段及位置；删除或无权时不返回内容。 |
| 检索 | `POST /knowledge-bases/{kb_id}/search` | 成员或管理员 | 请求 `{ "query": "..." }`，返回当前库有效构建的片段和定位。 |
| 问答 | `POST /knowledge-bases/{kb_id}/answers` | 成员或管理员 | 请求 `{ "question": "..." }`，返回 `AnswerResult`。 |
| 任务查询 | `GET /knowledge-bases/{kb_id}/tasks/{task_id}` | 管理员 | F2 启用；返回任务、文档、构建状态与安全的失败原因。基础阶段可保留接口契约，未启用时不假装后台任务存在。 |

上传使用 `multipart/form-data`，文件字段名 `file`。第 8 步接受 `.md`、`.txt`、`.pdf` 且单文件最多 10 MiB；检查文本 UTF-8 与常见二进制伪装，PDF 检查基础头尾结构，但不解析、不能由此证明 PDF 含可提取文本。扫描件将在解析步骤明确拒绝。原文件只存私有目录，以服务端生成的键定位；下载接口每次检查成员权限，不公开静态映射。第 8 步上传成功返回 `201`、`document_id`、`status: uploaded`、`request_id`，重复上传返回 `200` 和已有 ID；列表用 `limit`（默认 20、最多 100）及 `offset`（默认 0）分页，返回 `items`、`total`、`limit`、`offset`、`request_id`。后续入库步骤成功后才会返回 `build_id` 与可检索状态。F2 引入后台入库后，可返回 `202`、`task_id`、初始状态和查询路径。调用者始终以文档状态而非上传 HTTP 成功与否判断是否可检索。

### 请求与响应示例

示例 1：单库问答，请求 `POST /api/v1/knowledge-bases/kb_a/answers`：

```json
{ "question": "差旅报销需要在几天内提交？" }
```

成功回答 `200`：

```json
{
  "status": "answered",
  "answer": "模拟制度要求在出差结束后 10 天内提交。",
  "citations": [
    {
      "citation_id": "c1",
      "document_id": "doc_1",
      "build_id": "build_1",
      "chunk_id": "chunk_3",
      "document_name": "模拟差旅制度.pdf",
      "snippet": "出差结束后 10 天内提交报销材料。",
      "source_path": "/api/v1/knowledge-bases/kb_a/sources/doc_1/build_1/chunk_3",
      "page_number": 2,
      "heading_path": null,
      "start_line": null,
      "end_line": null
    }
  ],
  "request_id": "req_123"
}
```

资料不足也返回 `200`，例如 `{ "status": "insufficient_evidence", "answer": "当前知识库没有足够资料回答这个问题。", "citations": [], "request_id": "req_124" }`。需要澄清时使用 `needs_clarification`，在 `answer` 中说明缺少的条件，引用为空。

模型服务超时示例 `504`：

```json
{ "error": { "code": "MODEL_TIMEOUT", "message": "模型服务暂时不可用" }, "request_id": "req_125" }
```

文档上传请求示例：`POST /api/v1/knowledge-bases/kb_a/documents`，`multipart/form-data` 中发送 `file=@sample.pdf`。基础范围的成功响应示例 `201`：

```json
{ "document_id": "doc_1", "status": "uploaded", "request_id": "req_126" }
```

F2 任务查询响应示例 `200`：

```json
{ "task_id": "task_1", "document_id": "doc_1", "build_id": "build_2", "status": "processing", "error": null, "request_id": "req_127" }
```

## 9. 建议目录结构

目录是未来实现的边界建议，不要求在本步创建。按编号任务逐步落地，避免一次性生成空模块。

```text
ragdesk/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/              # 路由、请求/响应模型、认证依赖
│   │   ├── contracts/        # ParsedSection、Chunk 等跨层契约
│   │   ├── services/         # 用例、授权、构建发布、问答编排
│   │   ├── repositories/     # SQLAlchemy 查询与持久化
│   │   ├── parsers/          # Markdown、TXT、文本型 PDF
│   │   ├── retrieval/        # 单库有效构建检索；后续混合检索
│   │   ├── llm/              # 模型 API 适配器和 fake 实现
│   │   └── agent/            # F3 时加入 LangGraph
│   ├── alembic/              # 模式迁移
│   └── tests/
├── frontend/
│   └── src/                  # React + TypeScript 页面与 API 客户端
└── docs/
```

## 10. PostgreSQL + pgvector 的取舍

模拟企业资料规模待测，优先把成员关系、文档状态、有效构建指针、片段和向量放在同一个数据库中。这样可以用同一事务发布完整构建和撤销删除文档，用 SQL 将知识库授权范围与向量候选绑定，并减少演示项目的部署部件。pgvector 官方文档支持精确最近邻查询及 HNSW、IVFFlat 索引；基础阶段先以精确查询建立正确性基线，索引及参数由真实数据量和测量结果决定。后续 F1 可结合 PostgreSQL 全文检索做混合检索。[pgvector 官方说明](https://github.com/pgvector/pgvector)、[PostgreSQL 事务说明](https://www.postgresql.org/docs/current/tutorial-transactions.html)。

暂不引入独立搜索集群，是当前规模未知、需要优先验证权限和数据一致性的工程取舍，不是性能结论。单库方案的潜在代价是大型向量索引、复杂排序或高查询负载可能与事务型工作负载争用资源；pgvector 近似索引在过滤知识库后也可能漏掉候选。届时记录语料规模、查询计划、延迟和召回实验，再决定是否增加索引、分区或独立搜索系统；迁移时仍须保持本契约的单库授权与完整构建发布语义。
