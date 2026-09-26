# RRF 融合（第 18 步）

## 实现与公式

纯函数 `app.retrieval.rrf.fuse(vector, bm25, top_k=5)` 接收统一 `RetrievedChunk`，每路最多参与前 20 项，按 chunk_id 去重。服务 `app.services.hybrid.search` 实际调用向量和 BM25 时，各传入 top_k=20；最终默认前 5，允许 1～20。

固定公式：

```text
rrf_score(d) = 1/(60 + vector_rank(d)) + 1/(60 + bm25_rank(d))
```

某路未出现时该项贡献 0，排名从 1 开始。同一路重复出现只保留最小排名且贡献一次，跨路相同 chunk_id 合并。纯函数校验排名为 1～20 的整数；来源或正文冲突抛出错误。RRF 不使用原始 cosine distance 或 BM25 分数参与融合计算，保留它们只供解释。

手算示例（同一请求、同一授权库）：

| 块 | 向量排名 | BM25 排名 | RRF |
| --- | ---: | ---: | --- |
| A | 1 | 缺失 | 1/61 |
| B | 2 | 1 | 1/62 + 1/61 |
| C | 缺失 | 2 | 1/62 |

输出 B、A、C。内部用 `fractions.Fraction` 累计并比较，避免数学同分因浮点计算顺序而改变排序；对外转换为 float。同分按 chunk_id 的字符串升序。数据库生成的 UUID 在同一索引快照内稳定；重新导入产生新 UUID 后，并列项顺序可能变化，不能把不同索引快照当作严格复现。

扩展后的结果示例：

```json
{
  "chunk_id": "实际 UUID",
  "document_id": "实际 UUID",
  "build_id": "实际 UUID",
  "knowledge_base_id": "实际 UUID",
  "document_name": "演示资料.md",
  "text": "真实片段正文",
  "page_number": null,
  "heading_path": ["报销条件"],
  "distance": 0.2,
  "bm25_score": -0.1,
  "rank": 1,
  "vector_rank": 2,
  "bm25_rank": 1,
  "rrf_score": 0.03252247488101534
}
```

示例数字用于解释公式，不是实际评测输出。rank 是最终排名；vector_rank/bm25_rank 是各路原始排名，缺失为 null。rrf_score 不是正确概率。

## 授权与可配置降级

`search(factory, user_id, kb_id, query, profile, embedding_factory, top_k=5, config=FusionConfig(), trace=None)` 返回 `FusionResult(items, trace)`。user_id 必须由后端认证取得。现有 HTTP `/search` 和 RAG 问答仍调用原向量服务，本步提供独立融合服务与评测入口，没有自动切换业务策略或接入重排模型。

两路前读取授权当前语料；串行调用现有两路服务后重新授权并核对整个文本/构建集合，再校验所有候选的真实来源。两路计算不在长数据库事务中；没有增加跨请求缓存。失效语料返回 `RRF_CORPUS_CHANGED`，候选来源不一致返回 `RRF_SOURCE_MISMATCH`。

默认 `FusionConfig(allow_degraded=False)`：任意一路故障使本次失败。调用方显式传入 `FusionConfig(allow_degraded=True)`，才允许以下临时错误降级：

- 模型适配层 `MODEL_TIMEOUT`、`MODEL_NETWORK_ERROR`、`MODEL_UNAVAILABLE`，且状态为 502/503/504。
- SQLAlchemy 连接池超时，或 DBAPI 的 PostgreSQL SQLSTATE 40001（序列化失败）、40P01（死锁）、55P03（锁不可用）、57014（查询取消/超时）。

未获知识库权限、JWT 认证失败、模型认证/配置/参数/维度错误、数据库权限或认证失败、内容变化、未知代码异常全部传播。授权和语料核验阶段自身失败也不降级；这意味着共享数据库不可用时不能绕过校验返回旧数据。这里不新增重试，已有模型适配层仍遵循原最多 3 次尝试。

降级后仍再次检查授权和当前集合。双路临时失败返回 `RRF_ALL_ROUTES_FAILED`，不能解释为资料不足。成功路为空是合法空路；若另一路失败，允许返回空列表但 trace 必须标明 degraded，而不是宣称完整两路成功。

trace 示例形状：

```json
{
  "status": "complete",
  "allow_degraded": true,
  "degraded": true,
  "candidate_limit": 20,
  "rrf_k": 60,
  "top_k": 5,
  "routes": {
    "vector": {"status": "transient_failure", "error_code": "MODEL_TIMEOUT", "items": [], "elapsed_ms": 30},
    "bm25": {"status": "ok", "items": [], "elapsed_ms": 20, "cost": {}}
  },
  "fusion_ms": 0.1,
  "total_ms": 55
}
```

以上耗时仅为字段示例。真实 trace 保存原始各路候选和排名，错误只保留安全错误码，不保存异常中的密钥/连接串。失败时 service 会抛出错误；调用方可传入空字典读取异常 trace。`degraded=true` 记录发生过降级尝试，最终是否成功必须同时看 status。

## 公平的 dev 对比

`app.evaluate_rrf` 每题只执行一次完整融合请求：

1. 同一用户、库、query 取得向量最多20与BM25最多20。
2. 各路前5作为单路对照，RRF 使用这两份原始候选取前5；不为对照重复 Embedding 或重新分词。
3. 语料/模型/切块/编码约定与索引版本保持一致；BM25 继续 jieba-identifiers-v1、k1=1.5、b=0.75、epsilon=0.25，RRF 常数60，没有调参。
4. 正式指标只计三路完成、未降级、有 gold 的配对题，使用第16步原文和位置匹配；同时报告 paired_n、excluded_n、错误/未运行/降级数量。
5. 逐项报告 Hit@5、Evidence Recall@5、MRR@5，及 `rrf_minus.vector`、`rrf_minus.bm25` 的有符号差值。零与负值均保留，不据单项上涨声称总体提升。

配对题可能因故障被排除，所以比较时必须同时看样本量和排除数；不能把高失败率方法剩下的少数题当作总体质量。问答拒答、引用支持度、事实正确性不属于本步检索对比。

每题前后用原有 bind_index 校验完整有效索引摘要，覆盖模型配置、向量、正文和定位。索引变化记 INDEX_CHANGED，配对指标不计。正式模式要求全部原文件哈希与清单匹配、全部 active build 使用兼容真实模型配置；BM25 单独支持文本索引，但此次对比必须使用与向量路相同的有效语料。

## 命令与产物

在 `backend` 目录（PowerShell）：

```powershell
# 默认 dev 预检；不调用模型。
uv run --locked python -m app.evaluate_rrf

# 完成人工复核、真实入库，且已设置 DATABASE_URL/JWT_SECRET/OPENAI_API_KEY 后：
$env:EVAL_BEARER_TOKEN = $session.access_token
$env:RETRIEVAL_EMBEDDING_BACKEND = "openai"
uv run --locked python -m app.evaluate_rrf --run-real --mapping ../artifacts/eval-kb-map.json

# 仅当需要记录降级诊断时显式开启；降级题不进入配对效果分母。
uv run --locked python -m app.evaluate_rrf --run-real --allow-degraded --mapping ../artifacts/eval-kb-map.json
```

`--questions` 可指定已人工复核文件；`--split test` 需显式选择并通过冻结摘要，不用于调参。每次默认建立 `artifacts/eval/rrf-*`，或用 `--output` 指定尚不存在的目录，旧目录拒绝覆盖。退出码0表示所有请求完成（可能有明确标记的降级），1表示含逐题错误，2表示预检阻塞或未运行。

真实 dev 15题最多15次逻辑 query Embedding、原有重试上限下最多45次 HTTP 尝试，不调用 Chat 或文档 Embedding。没有 API 密钥或已复核样本时，脚本阻塞，不回退 fake。

`--fake-diagnostics` 与 `--run-real` 互斥，仅允许 dev；必须连接明确的 `fake:sha256-onehot-v1:1536:raw-v1` 索引。它保存 fake 向量、真实 BM25 与 RRF 的流程候选，正式效果/差值为 null，不评价语义排序。fake 的 usage 是测试值，不能作为真实 token 成本。

产物包含 results.jsonl、summary.json、report.md、manifest.json 和原始 input_questions.json：记录三路候选/排名、降级 trace、数据/代码/锁文件哈希、依赖和词典版本、模型与超时/尝试配置、实际切块参数、构建/向量摘要、模型原始响应和 usage。未运行指标为 null。实际调用的 token 仅为提供商已报告部分，失败/重试可能存在未知用量，unknown_usage_calls 单列，不能当完整账单。

单路 elapsed_ms 是同一次融合内的观测，RRF total_ms 包括两路和额外授权/一致性复核，fusion_ms 只记录纯排序开销；不能把这些值解释为三个独立服务的压力测试或并发 SLA。报告不修改第16/17步产物。

## 测试与依赖依据

```powershell
# TEST_POSTGRES_ADMIN_URL 指向 README 所述的专用测试服务器。
uv run --locked pytest -q tests/test_rrf.py tests/test_hybrid.py tests/test_rrf_evaluation.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
```

测试创建随机数据库并在结束时删除；未配置测试服务器时集成项明确跳过。设置 `STEP18_RRF_OUTPUT` 为新的绝对路径，可保留测试中的完整15题 dev 离线流程产物；资料按清单经过现有解析/600字符切块/80字符重叠及 fake 入库，数据库在运行后删除，不修改已有用户资料。

本步无新增依赖，沿用 uv.lock。有理数计算参考 [Python Fraction](https://docs.python.org/3/library/fractions.html)，异常类型参考 [SQLAlchemy Core Exceptions](https://docs.sqlalchemy.org/en/20/core/exceptions.html)，RRF 排名公式参考 [Elasticsearch RRF 文档](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)。使用的是本地公式实现，没有增加搜索集群依赖。

## 本次实际运行

- `artifacts/eval/step18-rrf-dev-fake/`：15条 dev 离线流程完成，模式 fake_diagnostics，每题恰好1次 fake query Embedding；BM25与RRF执行实际算法。向量最多20、BM25最多20、融合最多5，三路原始候选和原始排名均已保存。两库资料和切块参数沿用第17步（8份文档、600字符/80重叠）。这不是语义效果实验，正式三路指标和差值全部为 null。
- `artifacts/eval/step18-rrf-real-preflight/`：实际执行 `--run-real` 预检，退出2，15题全部 not_run；阻塞为 UNREVIEWED_SAMPLES、REAL_MODEL_KEY_REQUIRED。当前30题仍为draft，环境无真实密钥，没有发出收费请求。
- 原第16/17步共10个产物文件 SHA-256 核对未变，题目、复核状态和 test 冻结摘要未修改。

因此**目前无法判断 RRF 是否提升**，也不能把未测量表述为“没有提升”。脚本的手算汇总测试已验证：实际差值为零或负数时会原样保留；正式结论仍需已复核 dev 和真实兼容索引。

临时故障状态码还核对了 [PostgreSQL 错误码附录](https://www.postgresql.org/docs/current/errcodes-appendix.html) 与 [Psycopg errors/sqlstate](https://www.psycopg.org/psycopg3/docs/api/errors.html)。数据库权限错误42501不在降级白名单内。

### 回归状态

新增24项 RRF/融合服务/配对评测测试全部通过。全量回归两次均为 **136 passed、1 failed、1 skipped**：首次为 `test_relevant_chunks_can_still_be_insufficient[needs_clarification]` 意外401（request_id=e38babbafc4d4a5fb26c901760c4e783）；单项复跑通过；第二次为 `test_last_admin_cannot_be_removed_or_demoted` 意外401（request_id=2e1f19a30b9b4be9b882b8d9e8e6d6d5）。这与进度中既有偶发401现象相似，但本次未确定根因。

已检查认证代码，并做一次临时诊断：仅在 JWT 异常时记录异常类型和 iat/exp/当前时间，不输出令牌或密钥、不修改业务代码；15项旧问答/知识库测试通过，未捕获 JWT 异常。未放宽 JWT 校验或更改权限规则，也未继续重复跑全量测试。**本步不能声称全量回归通过**；后续需独立复现并定位偶发认证失败。跳过项为原有 opt-in 真实模型检索测试。
