# 向量检索基线评测（第 16 步）

## 当前状态与边界

评测器调用现有 `answer_question` 一次，通过只读 trace 获取同一次授权检索结果。仍为单库、当前有效构建、兼容 Embedding 配置下的 cosine distance 精确检索，固定 top_k=5；没有新增检索阈值、重排或提示词策略。

当前仓库 30 条题目均为 draft，dev/test 各 15 条。默认只运行 dev 预检，不调用模型；正式运行要求选定 split 的每条样本均已人工复核。脚本不会改写复核状态，也不提供 fake 冒充真实基线的运行模式。离线测试只验证程序行为。

## 准备与命令

1. 从 `data/eval/REVIEW.md` 逐题核对事实、证据和资料不足判定。真正完成后设置 `review_status: "reviewed"`、非空 `reviewed_by`、ISO 8601 格式 `reviewed_at`；不能批量改状态代替复核。
2. 将模拟资料按清单归属上传并用真实 Embedding 完成入库。所选知识库的全部未删除文档须与该库清单文件 SHA-256 集合完全一致，每份都有匹配配置的 ready active build。建议使用专门的评测库，避免混入额外资料。脚本不上传、不入库、不修改数据库。
3. 准备本地 JSON 映射，例如 `{"A":"实际知识库 UUID","B":"实际知识库 UUID"}`。评测身份须是所有所选库的成员，通过已验证 JWT 取得身份，每道题复核身份、权限与索引快照。
4. 设置既有 `DATABASE_URL`、`JWT_SECRET`、`OPENAI_API_KEY`、Chat/Embedding 模型配置；`RETRIEVAL_EMBEDDING_BACKEND=openai`。将登录获得的 access_token 放入 `EVAL_BEARER_TOKEN` 环境变量，不写入映射文件或报告。长时间运行若令牌到期，本次失败需如实保留，重新登录后另开报告目录。

在 `backend` 目录运行（PowerShell）：

```powershell
# 默认 dev 预检，无模型调用，自动创建独立产物目录。
uv run --locked python -m app.evaluate

# 仅在完成上述复核、真实入库和环境配置后显式运行。
# $session 是 README 登录步骤取得的响应对象。
$env:EVAL_BEARER_TOKEN = $session.access_token
$env:RETRIEVAL_EMBEDDING_BACKEND = "openai"
uv run --locked python -m app.evaluate --questions ../data/eval/questions.json --mapping ../artifacts/eval-kb-map.json --run-real
```

可以用 `--output ../artifacts/eval/dev-baseline-v1` 指定一个尚不存在的目录；已有目录拒绝覆盖。退出码：0 为全部问答完成，1 为已运行但含逐题错误，2 为未运行、预检阻塞或中断。默认预检退出 2 是明确的“未执行模型”，不是效果失败。

每题最多一次 query Embedding 和一次 Chat 逻辑调用，每次沿用适配层最多 3 次尝试；没有结果时会跳过 Chat。dev 15 题上限为 30 次逻辑调用、90 次 HTTP 尝试，实际数记录在逐题 `calls`。只有显式 `--run-real` 才允许这些收费请求。评测不重新计算文档向量。

`--split test` 才会选 test，必须校验题目文件旁的 `test.freeze.sha256`。摘要算法沿用第 7B 步：test 行按原顺序，JSON `ensure_ascii=False, sort_keys=True, separators=(",", ":")` 后 UTF-8 SHA-256。复核元数据也在冻结范围内；首次完成人工复核或纠正标注时记录修订原因，有意识更新摘要。脚本不自动重冻。不得依据 test 模型错误反复调参。

## 证据匹配与指标口径

| 项目 | 可执行口径 |
| --- | --- |
| 稳定文档身份 | 标注 document_id → 清单原文件 SHA-256 → 当前库数据库 document_id；文件名不参与相关性判断 |
| 原文位置 | 按模拟资料的 Markdown 二级标题或 TXT 中文章节标题定位，重新解析为 section_index 与字符区间；找不到引文或章节即阻塞 |
| 一个证据单元命中 | 同一候选块包含完整标注原文（比较时忽略空白）且其 source_spans 完整覆盖该原文区间；文档身份必须匹配 |
| Hit@5 | 前五个结果中有至少一个相关块为 1，否则 0 |
| Evidence Recall@5 | 每题命中证据单元数 / 标注证据单元数；重复的文档、章节、原文单元去重；汇总取逐题宏平均 |
| MRR@5 | 第一个相关结果排名倒数；前五名无相关结果记 0 |
| 检索分母 | 只含 gold 非空且检索完成、快照未漂移的题；Chat 失败不抹掉已完成的检索结果 |
| 资料不足拒答 | `unanswerable.insufficient_evidence` 为拒答数；`refusal_rate` 以该组完成问答数为分母 |
| 错误拒答 | `answerable.insufficient_evidence` 及该组 `refusal_rate`；`needs_clarification` 单列，不自动认定合理或错误 |
| 引用合法性 | 对尝试回答或提交引用的模型输出检查本次 evidence ID、唯一性及后端返回的真实来源 ID/片段；缺失、伪造引用记 false，无适用输出为 null |

首版按“完整证据单元”严格计分。若一条长引文被切到两个块，且没有单个候选完整覆盖，则记未命中，即使两个块合起来足够作答。该选择利于复现，但可能低估跨块证据召回；需人工复核标注粒度，不能看 test 结果后随意改匹配规则。

每组同时报告样本量、完成量、错误量和未运行量。没有 gold 的题不进入检索指标分母；技术错误不算拒答。小样本结果不代表总体效果。**合法引用仅检查来源，不能证明引用支持结论或事实正确**；每题 `manual_review` 保留待审核项，需独立人工判断，不调用另一个模型冒充已核实结论。

## 输出和复现信息

- `results.jsonl`：逐题输入/标注及定位、检索块和距离/rank、模型调用次数/耗时/usage、适配层结果、HTTP 原始响应正文、最终回答或安全错误码、各项指标、人工复核状态。HTTP 错误与错误 JSON 也保存；网络异常没有正文时留空。已知 API 密钥从 HTTP 正文中脱敏，不保存请求头。
- `summary.json`、`report.md`：指标宏平均、分母、拒答/澄清/错误分布、引用合法率、耗时和 token 汇总。`null` 表示未测量或未知，不是 0。
- `manifest.json`：split、UTC 时间、数据/语料清单哈希、代码 commit 和脏状态、实现源码及锁文件 SHA-256、Python 版本、固定检索策略、上下文预算、安全的模型配置、每份 active build 的 ID、模型/维度/配置版本、解析和切块参数，以及正文/向量/定位快照摘要。
- `input_questions.json`：本次题目文件原始内容，方便逐题复核。清单内容也存入 manifest；语料由清单 SHA-256 对应仓库版本。

每题前后核验索引快照（含正文、向量、定位及构建元数据）；发生漂移记 `INDEX_CHANGED`，本题检索/引用指标不计入有效统计。原始输出保留。先前已完成的题保留其当时快照和结果。

`retrieval_ms` 包括成员授权、query Embedding 与 SQL；`total_ms` 包括整个问答服务（鉴权、检索、上下文、Chat 和来源复核），不包含评测专用快照哈希、JSON 落盘、HTTP 路由或前端网络开销。它是脚本观测的服务耗时，不是生产端到端 SLA。每次模型调用另记耗时和 HTTP 尝试数。

token 使用提供商 usage；输入/输出/总量在逐次调用中保存。失败或重试可能发生未报告的消耗，因此累计总量在不完整时为 null，并单独报告已知部分与未知调用数，不能拿已知部分当完整账单。预检无模型运行时总 token 同样为 null，避免将未验证写成实测 0。

产物包含模拟资料正文和原始回答，默认写入已被 Git 忽略的 `artifacts/`。不得提交密钥、连接串或上传资料。报告目录逐题更新，普通模型故障会留下可复核的失败记录；进程被强制终止时以最后落盘记录为准。

## 程序验收与官方文档

```powershell
# 在 backend 目录。数据库测试需 README 所述的 TEST_POSTGRES_ADMIN_URL。
uv run --locked pytest -q tests/test_evaluation.py tests/test_evaluation_runtime.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
```

测试使用手算证据位置、可控 fake、HTTPX MockTransport 与随机临时 PostgreSQL 数据库；其指标数值不是模型效果。未配置测试数据库时集成项明确跳过。

本步沿用锁文件，无新增依赖。耗时使用单调计时器 [Python perf_counter](https://docs.python.org/3/library/time.html#time.perf_counter)，摘要使用 [hashlib SHA-256](https://docs.python.org/3.12/library/hashlib.html)。HTTP 响应 hook 在读取正文前触发，因此记录器显式读取正文后保存：[HTTPX Event Hooks](https://www.python-httpx.org/advanced/event-hooks/)。快照查询采用短会话，模型调用不持有数据库事务：[SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)。
