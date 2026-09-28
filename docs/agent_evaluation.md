# 第 25 步：Agent 评测与审查

## 本次结论

**评测器和离线安全回归已实现；真实效果对比未运行。** 仓库 dev 共 15 道题，全部为 draft；当前未配置评测数据库、JWT、真实模型密钥、评测用户令牌和知识库映射。不能把未运行解释为“Agent 没有提升”，也不能从 fake 的回答推断事实正确率。

当前保留固定 RAG 作为默认选择，不自动开启 Agent。依据是现有链路的复杂度和预算差异，尚无真实收益数据：

| 问题类型 | 当前选择 | 后续复核重点 |
| --- | --- | --- |
| 简单事实，一次检索已有完整证据 | 固定 RAG | Agent 增加决策请求是否有实际收益 |
| 缩写、表述模糊、首次查询缺少条件 | Agent 的候选应用场景；必要时澄清 | 查询改写是否找到新证据，澄清是否必要 |
| 跨文档规则、条件和例外 | 可对照试用 Agent | 补读是否恢复例外；最新检索覆盖旧证据可能丢失另一文档 |
| 资料确实不存在 | 固定 RAG 通常更合适 | Agent 是否只增加调用而仍应拒答；不得靠模型知识补企业规则 |

这些是待验证的设计判断。真实配对复核若 Agent 持平或更差，保留原始结果、胜/负/平统计和结论，不改题、不重跑到变好。

## 数据与报告

本次最终预检：`artifacts/eval/step25-preflight-final/`（如果目录已有，使用新的输出目录）。没有真实模型请求；15 行均为 `not_run`。

| 指标 | 固定 RAG | Agentic RAG |
| --- | --- | --- |
| 真实已执行 / 可比较配对 | 0 / 0 | 0 / 0 |
| 事实正确且有证据支持的比例 | 未运行、未人工复核 | 未运行、未人工复核 |
| 无答案拒答 / 可回答题错误拒答 | 未测量 | 未测量 |
| 平均工具调用 / 模型请求 | 未测量 | 未测量 |
| 平均 / P50 / P95 延迟 | 未测量，n=0 | 未测量，n=0 |
| usage / 成本 | 未知 | 未知 |
| 实际成功 / 失败轨迹 | 0 / 0 | 0 / 0 |

实际轨迹不足 3+3，不补造，不用测试脚本冒充真实问答成功案例。后续导入复核时，报告分别选择前 3 个成功/失败题号；逐题 JSONL 可查看对应 query、工具结果状态、证据 ID 变化、候选和引用、模型响应、错误、次数、usage 及耗时。技术失败可以列为失败轨迹，答案成功须人工 `task_success=true`。

已有 dev 分布为直接事实 6、同义改写 3、跨文档 3、资料不足 3。**同义改写不等于模糊表述**：当前没有独立复核的模糊题标签，该项覆盖缺失。本步不把未复核样本改为 reviewed，也不改冻结 test。

## 运行方法（PowerShell）

从项目根目录进入后端。环境准备沿用 README；依赖和锁文件未变。

```powershell
cd backend
uv sync --locked
uv run python -m app.evaluate_agent --output ..\artifacts\eval\step25-preflight-new
```

预检退出码 2 表示未满足真实执行条件，报告仍会保存。退出码 0 表示两条链路均执行完成；1 表示含技术错误；2 也用于配置阻塞、索引失效或中断。不是所有“执行完成”都意味着回答正确。

真实运行需要负责人先逐题核实原文/位置、expected_facts、answerable、split，生成已复核文件；每条 dev 标记 reviewed、reviewed_by、ISO reviewed_at，不能只批量改标签。保持原 test 不变。映射文件如 `{"A":"<数据库UUID>","B":"<数据库UUID>"}`，必须对应这份资料的完整有效真实索引。索引检查会拒绝混入其他资料、fake 向量、缺失块位置或配置不兼容。

在终端环境提供 `DATABASE_URL`、`JWT_SECRET`、`OPENAI_API_KEY`、`EVAL_BEARER_TOKEN`；不要写入源码或示例文件。模型配置为 `CHAT_MODEL=gpt-4.1-mini-2025-04-14`、`EMBEDDING_MODEL=text-embedding-3-small`、1536 维、`RETRIEVAL_EMBEDDING_BACKEND=openai`。当前评测命令拒绝未经该项目适配验证的其他模型配置。

```powershell
# 先以一对题检查真实配置；最多 12 次网络模型尝试，不是默认自动执行。
uv run python -m app.evaluate_agent --run-real --max-pairs 1 `
  --questions ..\artifacts\eval\reviewed-questions.json `
  --mapping ..\artifacts\eval\kb-map.json `
  --output ..\artifacts\eval\step25-real-smoke

# 正式 dev 对照：同一 15 题，两条链路最多合计 180 次模型尝试。
uv run python -m app.evaluate_agent --run-real --max-pairs 15 `
  --questions ..\artifacts\eval\reviewed-questions.json `
  --mapping ..\artifacts\eval\kb-map.json `
  --output ..\artifacts\eval\step25-real-dev
```

不同时运行资料删除、重建或权限变更实验。每条链路前后检查身份、资料和索引；发现漂移则保留原始输出、剔除这对并停止。每次运行创建新目录，不覆盖向量/BM25/RRF 的历史基线。

### 人工复核

复制运行目录的 `review.template.json` 为 `reviews.json`，逐条查看 `results.jsonl` 内的问题、expected_facts、gold_evidence、最终答案与真实引用原文。每条记录填写：

- `factual_correct`：回答的事实、金额、条件和例外是否正确；无事实回答需核实其拒答/澄清陈述是否准确。
- `evidence_supported`：引用原文是否确实支持回答中的每项事实；不因引用合法直接填 true。无事实回答审查其“资料不足/需补条件”的依据。
- `task_success`：本题是否被妥善解决，包括正确的资料不足拒答或必要澄清。可回答题被拒答不可标成功；answered 若事实或支持为 false，不可标成功。
- `reviewer`、`reviewed_at`、`notes`：人工复核者、ISO 时间、具体依据/问题。未复核三个标签保持 null。

```powershell
uv run python -m app.evaluate_agent `
  --review-run ..\artifacts\eval\step25-real-dev `
  --reviews ..\artifacts\eval\step25-real-dev\reviews.json
```

无需真实模型或数据库，生成新的 `reviewed-*.json/md` 和批注副本。脚本核对整个结果文件哈希及每条题目/输出哈希，拒绝重复 ID、缺少复核者、标签矛盾或输出被修改。哈希用于防止错配，不是复核人的身份认证或数字签名。

### 统计口径与公平性

- 同一批 dev、同一库和构建、同一基础 Chat/Embedding、精确 cosine；两条链路交替先执行，串行、无预热、不调参、不加入重排。记录 git commit、dirty 状态、实现源码及锁文件 SHA-256、题集/资料/映射/索引哈希、模型/切块/上下文配置。
- 核心对照仅使用双方已尝试且索引有效的配对。独立记录未配对或失效数量，避免一侧提前终止产生不同题集。按类别同样报告质量、拒答、调用、usage、延迟和样本量。
- 正确且有据比例的分母为**已人工复核的 answered 回答**；同时列出全部 answered 数。只有全部回答完成复核时才给 `ratio_on_all_answers`。不能用高比例掩盖大批错误拒答，因此另报可回答题错误拒答和人工任务胜/负/平。
- 无答案拒答率分母为已完成的不可回答题；错误拒答率分母为已完成的可回答题。技术故障单列，不当拒答；needs_clarification 单列，不自动判成功。
- 模型请求数包含决策、Embedding、最终 Chat 和每次 HTTP 重试；并非仅 Chat 次数。工具数固定链路为 0，但该链路实际做一次服务检索，JSONL 另有 `retrieval_call_count`。
- 耗时测量范围为后端问答服务调用，包括权限和最终来源校验；不含评测索引扫描、产物落盘、HTTP 传输到用户。报告所有有效尝试（含错误）及成功完成请求的 mean/P50/P95。P50 为中位数，P95 采用排序后 `ceil(0.95*n)` 位；n=1 时 P95 就是该值，不能外推总体尾延迟。
- 不同的网络请求与重试可能使响应 usage 不完整；任何未知项导致总 usage/总成本为 null，单次已知部分仍保留。仅使用环境 `TRACE_PRICES` 中在调用日期有效的价格，保存 as_of/valid_until、币种；没有价格不猜费用，不把 fake 当免费真实请求。
- **未消除的系统差异**：固定 top_k=5；Agent 工具默认 5，允许模型在既有 1～20 范围选择；工具预览 240 字、补读 1600 字、累计正文 12000 字，最新搜索替换旧证据。固定链路使用较完整的块。最终生成共享 ContextBudget，但不是每次输入证据完全相等的消融实验。Agent 还有整轮 60 秒截止，固定链路沿用单请求超时。记录这些差异，不能把效果变化全部归因于自主循环。
- 原始产物包含私有题目、模型响应及证据，只存受控本地目录。捕获的是现有 HTTP 适配器返回的已解析响应体，不含请求头、完整提示词；连接失败、非法 JSON 或重试前失败响应可能没有原文；截止后的迟到响应不纳入快照。没有采集模型私有思维链。

## 安全审查与回归

新增 `tests/test_agent_evaluation.py`，调用真实权限与现有服务，使用独立 PostgreSQL 测试库、fake 或 HTTP MockTransport。

| 场景 | 注入方式与检查 | 边界 |
| --- | --- | --- |
| 恶意资料指令 | 检索结果要求调用 shell；fake 模型故意服从并输出 shell 调用；后端拒绝未知工具，未执行外部操作 | 验证工具白名单，不证明真实模型永不受提示注入影响 |
| 伪造工具参数 | 模型输出 user_id/kb_id、top_k=21 或字符串类型 | 在工具执行前拒绝，不采用模型身份 |
| 跨知识库 | A 库搜索后模型请求 B 库真实 chunk_id | 拒绝读出，无外库正文或最终答案 |
| 无限循环倾向 | 规范化后相同 query；连续三个不同 query 且始终无证据 | 重复调用不再次执行，工具上限强制终止；无证据不调用最终 Chat |
| 合法引用但虚假金额 | fake 最终回答写 999999 CNY，真实引用只含 680 CNY | **复现已知语义盲点**：来源校验仍接受；必须人工判错，不改成声称“注入已彻底防住” |

其他回归复用 24B 的调用中撤权、文档删除、来源变化、六请求含重试、时间上限、上下文预算及失败后改写。没有添加新工具或修改决策策略。

## 官方依据与版本

核对 [Python 3.12 statistics](https://docs.python.org/3.12/library/statistics.html) 的均值、中位数和小样本统计行为，以及 [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) 的状态图接口。真实调用沿用已经文档化的模型适配器，不切换 SDK；无新增依赖，继续使用 `backend/uv.lock`。本项目明确定义自己的 nearest-rank P95，未混用默认插值方法。

## 可复制的离线验收

以下容器专供测试，不指向现有用户数据。fixture 会创建随机数据库并在结束时删除；`TEST_POSTGRES_ADMIN_URL` 仅为测试连接配置。

```powershell
# 在 backend 目录执行；如同名容器或端口已被占用，请换测试名称/端口。
docker run -d --rm --name ragdesk-step25-check `
  -e POSTGRES_PASSWORD=step25-test-only -p 127.0.0.1:55445:5432 `
  pgvector/pgvector@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6
# 确认 ready 后执行 pytest；不是依赖固定时长 sleep。
docker exec ragdesk-step25-check pg_isready -U postgres
$env:TEST_POSTGRES_ADMIN_URL = 'postgresql+psycopg://postgres:step25-test-only@127.0.0.1:55445/postgres'
uv run --locked pytest -q tests/test_agent_evaluation.py tests/test_agent_loop.py tests/test_agent_graph.py tests/test_agent_tools.py tests/test_evaluation.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
docker stop ragdesk-step25-check
```

没有设置测试库时数据库用例会跳过，不应把 skips 描述为安全回归通过。


## 实际验证记录（2026-09-28）

- 首轮新增评测与安全用例：20 passed（6.58 秒）。
- 新增用例扩展至 22 项，连同既有循环、单次图、工具与原评测器：120 passed（37.20 秒）。
- 最后补充统计比例与来源校验标记后，重新运行新增文件：22 passed（6.39 秒）；未把此前 120 项结果冒充最后改动后的整组重跑。没有执行整个项目测试集。
- ruff check、ruff format --check（104 文件）、uv lock --check（77 包）、git diff --check 均通过。无依赖、业务服务或数据库迁移变更。
- 所有模型测试均为 fake 或 MockTransport；数据库为本步专用容器及 fixture 随机库。没有真实收费 API 请求。
- 最终预检为 not_run，15 条未运行、0 个有效配对；数据 SHA-256：`455ea770c675676303e1f61fdc08e1db01e556ba70bd4f5d2f4912aaca2f25db`。
- 代码基点 `db4038e8e2e22e4aee4510ec2a75ca36b1955e5f`，dirty=true（本步未提交）；最终实现与锁文件内容哈希：`99c0093d6c01e91495dbcbada26663de9e454fafd50dbc9556e21812a7d4385f`。文件级哈希见 manifest。
- 原始测试日志保留于 `artifacts/validation/step25/`；预检逐题记录及原始题集快照在 `artifacts/eval/step25-preflight-final/`。这些目录已被 Git 忽略。
