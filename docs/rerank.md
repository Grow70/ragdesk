# 第 19 步：可关闭重排器与实验记录

## 实现与边界

`app.retrieval.reranker.Reranker` 接口接收问题和最多 20 个候选正文，返回 `RerankResult(scores, search_units, raw_response)`。`scores` 为零起始候选索引及分数。Cohere 适配器已经按官方接口实现，并使用 HTTPX MockTransport 验证；**真实接口未验证**。`FakeReranker` 默认反转候选次序，仅用于检查流程，不衡量语义相关性，不会自动替代失败的真实客户端。

`app.services.reranked.search_configured(..., settings)` 读取 `settings.rerank_enabled`；底层 `search(..., reranker_factory, enabled=False)` 便于注入 fake。关闭时不创建重排客户端、不要求 Cohere 密钥。开启时先取得同次 RRF 融合的前 20 项，再重排取 5 项；不改现有向量 `/search` 与问答策略，不加入重排 HTTP 接口或其他后续能力。

- 每项必须对应原候选索引，数量完整、无重复、数值有限。伪造索引或非法响应直接失败。
- 只按分数排序，同分保持原 RRF 顺序；元数据仍取自数据库候选。保留 `rrf_rank`、`vector_rank`、`bm25_rank`、`rrf_score` 与 `distance`，新增 `rerank_score`。重排分数越大排序越靠前，不是答案正确概率。
- 向外部服务发送正文前重新授权并校验来源，返回前再次校验；权限撤销和有效源变化不可降级绕过。网络期间无数据库事务。
- 单次调用，不重试。超时、网络异常、408/429/5xx 回退 RRF 前 5，`trace.degraded=true`，`trace.rerank.status=fallback` 且带错误码。401/403/498、参数或响应错误直接失败。
- `trace` 记录所有 RRF 候选、原始重排响应、调用次数和耗时。日志不输出正文或密钥；评测原始产物可能含正文，放在 Git 忽略的 `artifacts/eval/`。

## 配置及官方依据

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RERANK_ENABLED` | `false` | 服务配置入口的开关 |
| `COHERE_API_KEY` | 无 | 只从进程环境读取 |
| `RERANK_MODEL` | `rerank-v3.5` | 本步只允许此模型，不按未经核验的模型名运行 |
| `RERANK_CONNECT_TIMEOUT_SECONDS` | `3` | 连接及连接池等待超时 |
| `RERANK_READ_TIMEOUT_SECONDS` | `10` | 读写阶段超时 |

2026-09-27 核对 [Cohere v2 Rerank API](https://docs.cohere.com/v2/reference/rerank)：`POST https://api.cohere.com/v2/rerank` 使用 Bearer 认证；传入 `query/documents/model/top_n/max_tokens_per_doc`，返回零起始 `index/relevance_score` 和可选计费 `meta.billed_units.search_units`。本实现要求返回全部候选用于完整性检查。

[`rerank-v3.5` 官方模型列表](https://docs.cohere.com/docs/models) 标示 4k 上下文；[专用重排说明](https://docs.cohere.com/docs/rerank-overview) 列出中文支持。这说明接口提供语言能力，不能代替项目语料上的实测。每项设置 `max_tokens_per_doc=4096`，服务端可截断超长文档；项目默认 600 字符切块不代表准确 token 数，也不保证任何长度输入都被完整处理。问题过长导致的服务参数错误会失败，不当成可用降级结果。

[HTTPX 四类超时](https://www.python-httpx.org/advanced/timeouts/) 是各网络阶段/等待的限时，不是完整请求的严格墙钟上限。复用 `backend/uv.lock` 中 HTTPX，没有添加 SDK 或其他依赖。

## 验收及复现（PowerShell，进入 backend）

无需密钥的单元检查：

```powershell
uv sync --locked
uv run --locked pytest -q tests/test_reranker.py tests/test_reranked.py tests/test_rerank_evaluation.py
uv run --locked ruff check .
uv run --locked ruff format --check .
```

数据库项需要 `TEST_POSTGRES_ADMIN_URL`，测试会建立/删除独立随机库；无配置时明确跳过。设置方式见 README 的数据库验收说明。

单次真实接口检查需要提前在进程环境配置 `COHERE_API_KEY`，明确开启后只调用一次、发送两段演示文字：

```powershell
$env:RUN_REAL_RERANK = "1"
uv run --locked pytest -q -s tests/test_reranker.py::test_real_cohere_chinese_contract
Remove-Item Env:RUN_REAL_RERANK
```

该检查保存输出到终端，验证响应契约；即使通过，也不等于 dev 检索提升。

默认预检（不调用模型；有阻塞时退出码 2）：

```powershell
uv run --locked python -m app.evaluate_rerank
```

真实 dev 比较需已人工复核的问题文件、真实 Embedding 索引、A/B 到数据库 UUID 的映射、`DATABASE_URL`、`JWT_SECRET`、`EVAL_BEARER_TOKEN`、`OPENAI_API_KEY` 和 `COHERE_API_KEY`。映射格式沿用 `{"A":"库 UUID","B":"库 UUID"}`。配置和复核流程见 [评测说明](evaluation.md)。然后执行：

```powershell
uv run --locked python -m app.evaluate_rerank --run-real --questions C:\ragdesk-private\reviewed-questions.json --mapping C:\ragdesk-private\kb-map.json
```

`--run-real` 明确授权本次比较的实验组开启重排；基线关闭，不受服务默认关闭影响。每题共用一次 RRF20，额外最多一次 Cohere 调用，现有向量 Embedding 最多 3 次尝试。15 题最多 15 次重排调用，可能产生费用；不运行 Chat。`--allow-degraded` 只控制上游 RRF 的单路降级，权限错误仍不降级；重排临时故障始终回退并排除正式配对分母。

离线流程诊断需要隔离的 fake 索引，可用 `--fake-diagnostics --mapping ...`，限定 dev，不允许标记为真实模型。不修改样本复核状态。每次输出新目录，拒绝覆盖已有目录。

## 实验设计与记录（2026-09-27）

- 对照：同次 RRF 前 5；实验：同次 RRF20 重排后前 5。模型、切块、预处理和候选集合相同，无重排调参搜索。
- 检索质量：Hit@5、Evidence Recall@5、MRR@5，匹配原文与定位；只统计已复核、真实执行、未降级且有 gold 的配对题，报告分母和有符号差值。没有 gold 的题不进入召回分母。
- 时间：记录 RRF、重排调用增量及全流程时间/样本量；全流程还含额外授权检查，不能把重排调用耗时视为全部开销。一次小样本运行不代表并发或生产延迟。
- 成本：分开记录 Embedding token/尝试和重排调用次数、已报告 search_units、未知计费调用数。search_units 不是 token；没有计费信息或报价时金额为 null，失败可能计费。基线与实验共用的 Embedding 不重复计费统计。
- 产物保存 `manifest.json`、`input_questions.json`、`results.jsonl`、`summary.json`、`report.md`；包括代码版本与源码哈希、题目/资料/映射哈希、模型配置、索引/切块参数、两组原始候选及响应。引用支持程度、事实正确性仍需独立人工复核。

### 实际执行

1. `artifacts/eval/step19-rerank-dev-fake/`：dev 15 题全部完成 fake 流程；RRF/RRF+rerank 的正式质量指标及差值均为 null，配对分母 0；15 次 fake Embedding、15 次 fake 重排，没有外部收费调用。耗时仅为本机 fake 流程测量，不能当作真实模型延迟。
2. `artifacts/eval/step19-rerank-real-preflight/`：真实预检阻塞于 `UNREVIEWED_SAMPLES`、`REAL_MODEL_KEY_REQUIRED`、`RERANK_API_KEY_REQUIRED`。真实服务、真实质量、真实延迟与费用均未验证；不能宣称改善或没有改善。
3. 保留第 16/17/18 步原始产物；本步没有重写向量、BM25、RRF 基线。

**结论：默认关闭。** 目前没有经过人工复核的真实 dev 对比来证明质量收益足以承担额外延迟和费用。接口与 fake 流程完成；真实实现仍须用密钥执行单次检查，然后在已复核 dev 上做上述配对实验，才能作上线开关决定。

### 本步验证记录

专用临时 PostgreSQL 验收：定向测试 `35 passed, 1 skipped`；全量回归 `172 passed, 2 skipped`，均有 1 个既有 TestClient 弃用警告。真实模型检查未配置而跳过。Ruff 检查/格式检查、依赖锁检查及 `git diff --check` 通过；原基线 20 份文件 SHA-256 不变。数据库测试使用随机库并在结束后删除；验收容器随后停止。
