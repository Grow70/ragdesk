# 请求级 trace（第 22 步）

## 启动与查看

无需额外监控平台，使用现有 PostgreSQL。先在 backend 目录迁移，再启动 API：

```powershell
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

在另一个终端请求问答并查看 trace：

```powershell
$base = "http://127.0.0.1:8000"
$token = Read-Host "Bearer token"
$kbId = Read-Host "已授权知识库 ID"
$headers = @{ Authorization = "Bearer $token" }
$body = @{ question = "报销期限是多少？"; top_k = 5 } | ConvertTo-Json
$answer = Invoke-RestMethod -Method Post -Uri "$base/knowledge-bases/$kbId/answers" `
  -Headers $headers -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$trace = Invoke-RestMethod -Uri "$base/knowledge-bases/$kbId/traces/$($answer.request_id)" -Headers $headers
$trace | ConvertTo-Json -Depth 12
```

每次问答使用服务端新生成的 request_id，忽略客户端 X-Request-ID。错误响应和 X-Request-ID 响应头也提供该标识；用错误响应中的 request_id 查询失败 trace。问答响应头 `X-Trace-Status=stored` 表示已提交，`unavailable` 表示存储失败，不能当成已有 trace。

查看者必须仍是当前库成员，且是请求本人或该库管理员。普通成员不能看同库其他人的 trace；跨库、未知 ID 或无权限均为 404，缺少身份为 401。管理员可查看该库失败请求的精简元数据。trace 内 request_id 指原问答请求，查看请求自身的 X-Request-ID 则是另一个 ID。

## 保存哪些数据

`answer_traces` 保存 request_id、可信 user_id（未认证为空）、请求的 knowledge_base_id（非法 UUID 为空）、created_at 和 JSONB payload。身份/库 ID 是审计快照，不设外键，这样不存在的库和未授权尝试也能记录。来源文档删除后 trace 可保留 ID，但不会恢复已删除的正文访问。

- 检索策略、HTTP 状态、answered/insufficient_evidence/needs_clarification 或 error、refused、degraded、timeout、安全错误码。
- 候选 chunk_id/document_id/build_id、排名、上下文引用编号与块映射，以及最终返回引用 ID。未使用的路线排名为 null。
- Chat/Embedding/重排各角色的 status、method_calls、call_count、逐次模型身份、用量、耗时和费用估算。
- 事件具有 event_id、parent_event_id、kind、name、offset_ms、duration_ms、status、error_type；kind 支持 stage/model/tool，给未来 Agent 留结构，本步没有执行工具。
- 默认不保存问题、答案、提示词、文档正文/文件名、向量、请求头、密码、密钥、供应商原始响应或异常消息。采用字段白名单，未将离线评测的原始输出 trace 直接写库。

当前 HTTP 问答仍是 vector；重排在本链路为 not_run、call_count=0、usage=null。重排适配器计量包装可记录 model/call_count/search_units，使用受控测试验证，没有自动切换问答检索策略。当前无降级路径时 degraded=false、degradations=[]，不虚构降级事件。

## 耗时如何读

`perf_counter` 测量持续时间，UTC created_at 用于时间定位和价格有效性。total_ms 从 HTTP 中间件进入到响应生成，包含框架与认证；不包含 trace 写入耗时和响应传输。trace 写入在独立短事务中进行，模型调用不会持有该事务。

- request_preparation：进入中间件到问答路由开始，涵盖路由、参数处理、JWT 验证及用户数据库查询，不能全解释为模型或纯 SQL。
- retrieval：当前检索服务的完整耗时，**已经包含 Embedding**。
- model.embedding / model.chat：实际适配器方法的墙钟耗时，包括其重试和等待；不将适配器自报 elapsed_ms 当作额外阶段相加。
- retrieval_non_model：本串行向量流程的 retrieval 减 Embedding 耗时，包含成员校验、候选查询、向量 SQL 及编排开销；不是纯数据库执行时间。
- context / validation / source_validation：上下文构造、回答结构/引用校验、当前权限与证据复核。
- model_calls：本串行流程的所有模型事件之和，不能再与包含它的父阶段一起累加。

### 一次实际执行的请求

原始精简 trace 保存在 `artifacts/validation/step22/request-sample.json`（Git 忽略目录），request_id 为 **4aaffc50b2ae455f9966f7d9d36d8d1b**。这是 TestClient 实际经过 HTTP 中间件、JWT 授权、临时 PostgreSQL 向量查询、受控 Chat 和 trace 写库/读取的请求，n=1；Embedding/Chat 均为显式 fake，没有真实模型调用。

| 阶段 | 实测毫秒 |
| --- | ---: |
| 总耗时 | 57.889 |
| 请求准备（含认证） | 33.887 |
| 检索整体（含 Embedding） | 15.154 |
| 其中 Embedding | 0.035 |
| 检索扣除 Embedding | 15.118 |
| Chat 模型调用 | 1.521 |
| 所有模型调用合计 | 1.557 |
| 来源/权限复核 | 5.568 |

判断方法：比较互不重叠的 `retrieval_non_model=15.118 ms` 和 `model_calls=1.557 ms`，这次请求的检索非模型部分更长；但完整请求最大的一段是请求准备 33.887 ms，因此不能声称总耗时主要来自模型，也不能把这段时间全归因数据库。若调查这一请求，应先细看认证/连接建立及请求准备，而非调整模型。单个本机 fake 样本不能推断真实 API 的瓶颈、P95 或 SLA。

## usage 与调用数

- method_calls 是接口方法调用次数；call_count 是适配器报告/计数器增量的尝试次数，包含重试。计数未报告则 null，不用 0 代替。
- 单次成功且使用量完整时 usage_status=reported；重试只拿到末次 usage 时，逐次记录保留该 usage 并标 partial，角色汇总视为未知。
- 超时、非法响应等未获得使用量时 usage=null；fake 的零 token 不代表真实免费，标 simulated，usage 和费用为 null。
- 没有执行的角色明确 not_run。此时 call_count=0 是已知没有调用，usage=null。
- 外部重试可能产生无法观测的费用；完整 usage 仅指本适配器记录完整，不是供应商最终账单保证。

## 可选价格配置

默认 `TRACE_PRICES={}`，所有有调用而无匹配价格的费用为未知。应用从环境读取 JSON，不自动查询价格，也不设置供应商默认价格。以下是**仅演示配置格式的虚构价格，不是供应商报价**：

```powershell
$env:TRACE_PRICES = '{"demo:model":{"currency":"USD","as_of":"2026-09-28","valid_until":"2026-09-30","unit":"tokens","input_per_million":"2","output_per_million":"8"}}'
```

真实使用时由项目负责人核实所用模型价格，再将键替换为精确的 `provider:model`（例如当前实际适配器的模型标识），填入核实日期、有效截止日期和币种。单位 token 时两种单价都需显式提供；Embedding 的输出单价可以在确认为无输出计费时显式填 0。重排使用 `unit=search_units` 和 `per_search_unit`，不能把 search_units 当成 token。非法配置使启动失败，错误只提示 TRACE_PRICES 字段，不回显配置内容。

估算使用 Decimal：`input_tokens × input_per_million / 1e6 + output_tokens × output_per_million / 1e6`；重排为 `search_units × per_search_unit`。每次模型记录都保存所匹配的价格、as_of 和 valid_until。请求 UTC 日期不在区间、未知 usage、模型不匹配或单位不匹配时 amount=null 并给出 reason。多个币种不直接相加；费用汇总提供 known_subtotal、estimated_total、currency、complete，任一未知则总额保持 null。配置变更不会改写历史 trace 的价格快照。

## 验收与边界

在 backend 目录，使用能够创建临时数据库的测试服务器（fixture 不操作已有用户数据）：

```powershell
$env:TEST_POSTGRES_ADMIN_URL = Read-Host "临时 PostgreSQL 的 psycopg URL"
uv run --locked pytest -q tests/test_traces.py tests/test_answers.py
uv run --locked ruff check .
uv run --locked ruff format --check .
```

测试覆盖字段白名单、真实 PostgreSQL 保存/查询、本人/管理员/其他成员/跨库/撤权、拒答/澄清/空库/超时/错误/缺失令牌/非法请求、计数和 usage 未知、重试、价格有效期/非有限值/多币种、重排计量、工具事件结构、写库失败，以及独立测试库迁移回退再升级。

写库失败保持原问答结果，返回 unavailable 并只记 request_id 与异常类型。强制终止可能丢失尚未保存的 trace；尚未实现保留期自动清理，数据库占用会随请求增加。本步没有额外监控平台、后台追踪导出或 Agent 调用。

官方依据：[SQLAlchemy 短事务与会话](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)、[Python perf_counter](https://docs.python.org/3.12/library/time.html#time.perf_counter)、[Decimal 算术](https://docs.python.org/3.12/library/decimal.html)。未新增依赖，沿用 uv.lock。
