# 最终实验与证据归档（第 30 步）

日期：2026-09-30。**效果实验状态：未执行；证据完整性检查与无模型预检已执行。**

本报告使用纯虚构的模拟企业资料，不包含真实员工或企业内部信息。真实已执行题数 A/B/C/D 均为 **0**，可比较配对 **n=0**；没有可报告的效果提升、真实模型延迟或费用数字。不是“四种方案都失败”，也不能据此认定方案等效。

## 1. 前提核对与冻结状态

用户已声明 dev 调参结束、数据和配置冻结。本次仓库交接状态如下；如果正式冻结材料存于外部，需提供位置后另行核对。本报告不代替人工签署冻结记录。

| 检查项 | 实际证据 | 状态 |
| --- | --- | --- |
| 题集结构、分组、逐字证据及源文件哈希 | `check_eval_set.py` 成功；[原始输出](evidence/step30/dataset-check.txt) | 已通过自动检查 |
| test 内容摘要 | `74576b49f4f043418eedd9f634d6ee2ab40aa3d7211a8e91d87d01e88e8bd098`，与原冻结文件一致 | 完整性通过 |
| 人工金标复核 | dev/test 各 15 条全部仍为 draft，test reviewed=0 | 未完成交接，不能推定已复核 |
| 最终 dev 配置与选择依据 | 仓库既有记录主要为 fake 流程/真实预检，未找到最终签署配置；已向负责人请求路径 | 尚未取得 |
| 真实运行环境 | 当前进程 DATABASE_URL、JWT_SECRET、OPENAI_API_KEY、COHERE_API_KEY、EVAL_BEARER_TOKEN 均未提供；未取得库映射/有效索引快照 | 不具备实际运行条件 |
| 四方案完整问答评测入口 | A 支持 test；B/C 只做检索评测；D 比较命令仅支持 dev | 存在能力边界，详见下节 |

`questions.json` 原文件 SHA-256 为 `455ea770c675676303e1f61fdc08e1db01e556ba70bd4f5d2f4912aaca2f25db`。test 摘要按原顺序选择 test 记录，采用 UTF-8 JSON、`ensure_ascii=False, sort_keys=True, separators=(",", ":")` 后计算 SHA-256；包括复核元数据。详见[输入快照](evidence/step30/questions.snapshot.json)、[审计记录](evidence/step30/audit.json)。

**本步没有修改题集、冻结摘要、模型提示词、检索/Agent 实现或参数。** 读取 test 用于完整性审查和预检；没有 test 模型输出可用于调参。历史上 test 是否曾被用于开发，无法仅凭摘要独立证明。如果之后依据 test 错误修改实现、提示词、参数或标注，必须记录日期、题号、输出摘要和变更 commit，并将该集合标为“已用于开发”；不能继续作为未见测试集。正常人工订正也须保留版本与原因，不能静默重冻。

## 2. 样本量与方案范围

资料清单含 **8 份模拟文档、2 个逻辑知识库**。题集共 30 条，dev/test 各 15；test 中 A 库 12 题、B 库 3 题。

| test 类别 | 题数 |
| --- | ---: |
| 直接事实 | 6 |
| 同义改写 | 3 |
| 跨文档综合 | 3 |
| 资料不足 | 3 |
| 合计 | 15 |

可回答 12 题，资料不足 3 题；非空 gold evidence 12 题。这些是**计划样本数**，实际检索、答案和人工复核统计分母目前均为 0。小样本将来即使完成，也不能推断真实企业总体效果或生产并发能力。

| 方案 | 已实现能力与本次动作 | 效果状态 |
| --- | --- | --- |
| A：向量 + 固定 RAG | 精确 cosine top5→证据→Chat→引用校验；执行 `app.evaluate --split test` 预检，退出 2 | **未执行** |
| B：BM25 + 向量 + RRF | 两路最多各 20、RRF k=60；执行 `app.evaluate_rrf --split test` 预检，退出 2。该命令只比较检索，不生成 Chat 答案 | **未执行** |
| C：B + rerank | Cohere `rerank-v3.5` 适配器已实现，真实提供商仍未验证；执行 `app.evaluate_rerank --split test` 预检，退出 2。只比较检索 | **未执行** |
| D：选定检索 + Agent | Agent 循环已实现，工具当前复用向量检索。仅核查 `app.evaluate_agent --help` 与源码；入口硬编码 dev，无 `--split test`，最终检索选择记录缺失 | **未执行** |

本步没有重标 test 为 dev、使用 fake 替代失败模型，或将 B/C 的检索结果当成完整问答结果。若后续要求 B/C 同时比较生成质量、D 在 test 上运行，需要先明确并完善对应评测入口，保留新代码版本；本步没有扩展业务实现。

## 3. 配置快照与可比性约束

[配置快照](evidence/step30/configuration.snapshot.json)记录**当前代码默认值，尚非已批准或实际执行的最终实验配置**。实际索引、构建 ID、运行模型维度、库映射和价格配置均为空，不根据默认值推测真实状态。

| 项目 | 当前实现/默认值 |
| --- | --- |
| Chat / Embedding | `gpt-4.1-mini-2025-04-14` / `text-embedding-3-small`；配置维度 1536，实际维度本次未测 |
| 编码 | `raw-v1`；query/document 直接编码，不使用不同前缀 |
| 切块 | 600 字符、overlap 80；批量 Embedding 16；字符不是 token |
| 检索 | 精确 cosine；最终 top5；RRF 两路最多各20，rank 从1开始，k=60；不相加两路原始分数 |
| BM25 | k1=1.5、b=0.75、epsilon=0.25，`jieba-identifiers-v1`，词典摘要已保存 |
| rerank | RRF20→重排→top5；默认关闭；只对授权候选排序 |
| 固定问答预算 | total=16384、output_tokens=1024、overhead=1024；现有代码以序列化消息/schema UTF-8 字节数保守核算输入，**不是精确 tokenizer 计数**；问题与系统提示占用预算 |
| Agent 上限 | 工具3、模型请求6（含重试与生成）、60秒、累计工具上下文24000字符 |
| 工具输出 | 搜索正文每项240字符、补读1600字符；response_chars=8000、total_text_chars=12000 |
| 提示词/代码/依赖 | 固定提示词摘要、后端源码树摘要与 uv.lock 已记录；不临时调整提示词 |
| usage / 价格 | 当前无运行 usage，无有效价格及日期；金额未知，不填0假装免费 |

在真正运行前，应固定同一资料 SHA 集合及各库 ready active build、兼容 Embedding 配置、切块/解析版本、Chat 和输出预算、证据匹配与人工评分规则。逐次授权和索引漂移检查失败时应停止/排除并记录，不能混合重建前后的结果。

D 与固定流程现有差异包括：工具预览长度、只保留最新证据、允许选择 top_k、额外整轮截止和模型决策请求。因此即使使用相同基础模型和最终生成预算，也不能称为只有循环次数不同的严格消融。B/C 目前只测检索，与 A/D 的整体问答延迟不能直接放进同一个性能比较分母。

## 4. 实际结果：全部未执行

| 指标 | A | B | C | D |
| --- | --- | --- | --- | --- |
| 真实执行题数 | 0 | 0 | 0 | 0 |
| Hit@5 / Evidence Recall@5 / MRR@5 | 未执行 | 未执行 | 未执行 | 未执行 |
| 事实正确且引用支持比例 | 未执行 | 未执行 | 未执行 | 未执行 |
| 无答案拒答 / 错误拒答 | 未执行 | 未执行 | 未执行 | 未执行 |
| 引用合法性与人工支持关系 | 未执行 | 未执行 | 未执行 | 未执行 |
| 平均工具数 / 模型请求数 | 未执行 | 未执行 | 未执行 | 未执行 |
| 检索耗时 / 总耗时 mean、P50、P95 | 未执行 | 未执行 | 未执行 | 未执行 |
| token usage / 重排 search_units / 可估费用 | 未知 | 未知 | 未知 | 未知 |

已保存 [60 条逐题状态记录](evidence/step30/results.jsonl)。每条绑定 test ID、方案、知识库、类别、阻塞原因以及原始预检位置；检索结果、答案、引用、调用数、耗时、usage、费用均为 `null`。原始 B/C 预检器中的空候选数组仅是 not_run 初始化值，不能解释为检索执行后没有召回。

### 固定评分口径

- 证据命中需稳定文档身份、原文逐字证据（忽略空白）与 section 字符位置全部对应；不能只匹配文件名。同一完整证据单元需由候选块完整覆盖，跨块拼合不自动算命中，保留该限制。
- Hit@5 为前5至少命中一个 gold；Evidence Recall@5 为命中去重 gold 单元数/该题 gold 单元数，汇总宏平均；MRR@5 为第一个相关排名倒数。无 gold 的题不进入检索分母，技术失败与未执行另列。
- 答案正确性、引用是否支持每个结论和任务成功分别人工标注；合法引用 ID 只能验证来源。所有方法使用相同复核标准，逐项核对金额、条件、例外、否定与冲突，记录复核者/日期和结果摘要。
- 比较报告必须同时列出计划题数、实际执行、技术错误、降级、已人工复核与配对分母；未复核结果不进入正式正确率。资料不足拒答与可回答题错误拒答分开，澄清独立列出。
- 延迟只统计实际完成记录，同时报告 n、mean、P50、P95 和超时数；小样本分位数采用排序后线性插值（位置 `(n-1)*p`）并注明方法。原始毫秒保留，预检耗时和 fake 耗时不能放入真实性能表。
- usage 缺失即未知；仅在供应商、模型、计费单位、币种、价格和价格日期匹配时估算金额；失败请求可能计费，未返回 usage 不能填0。
- 百分点变化：`(B率-A率) × 100`；相对百分比变化：`(B率-A率)/A率 × 100%`，A率为0时相对变化未定义。**说明性示例，非实验结果：**60%→75% 是增加15个百分点，相对增加25%。本次没有可计算的差值。

## 5. 人工复核与真实失败案例

[人工复核模板](evidence/step30/human-review.template.json)包含60个题目/方案槽位，全部 `pending_execution`；没有生成标准答案，没有人工评分结果。当前也未将原 draft 改成 reviewed。

真实输出产生后，先核实金标，再由负责人或独立复核者逐题检查答案事实与每个引用的支持关系，填写 `facts_correct`、`citations_support_conclusion`、`task_success`、复核人/时间及说明，并绑定原始结果 SHA-256。有歧义或分歧时保留双方记录和裁决；盲化方案标签可降低偏好，但未实际采用前不能声称已完成盲评。

**真实失败案例：0 条，原因是真实执行 n=0。** [失败清单](evidence/step30/failures.json)的 cases 为空。未评测不等于失败率0或成功率100%；不补造五例，不使用历史 fake 故障注入或 CI 失败替代真实问答案例。

将来实际失败少于5条则全部列出；达到5条时至少保留5条，并保留全部原始记录以避免只挑有利案例。分类包括召回遗漏、证据不完整、无依据事实、引用不匹配、错误拒答、应拒未拒、冲突处理、预算耗尽、供应商/超时和权限/索引变化。每例需关联题号、方案、原始输出、gold/检索片段、调用轨迹、分类和人工判断；本段仅定义分类，不是实际案例。

## 6. 版本、归档与复现

基线 commit：`21f640f81060773d391dbc6bd5506b15de06df74`（第29步提交）。预检时工作区干净；新增报告/归档文件不改变后端源码。[代码版本记录](evidence/step30/code-version.json)包含每个后端源码/依赖文件摘要、源码树摘要及执行 Python 版本。已提交的源码及模拟数据另保存为本地 `artifacts/eval/step30/source-baseline.tar`，摘要见版本记录；该大包在 Git 忽略目录，分享项目时需另行携带，或从上述 Git commit 重建。

| 产物 | 内容 |
| --- | --- |
| `reports/evidence/step30/{A,B,C}-test-preflight/` | 实际预检原始 manifest、输入、逐题结果、汇总和报告；均 not_run |
| `questions.snapshot.json`、`test.freeze.sha256`、`corpus-manifest.snapshot.json` | 未修改输入与资料身份快照；原文仍在受版本控制的模拟资料目录 |
| `configuration.snapshot.json`、`code-version.json`、`audit.json` | 配置来源、版本、摘要、样本量和前提核对 |
| `results.jsonl`、`human-review.template.json`、`failures.json` | 四方案逐题执行状态、未填写的人审槽位及空真实失败清单 |
| `historical-evidence-index.json` | 既有 dev/fake/预检产物路径和哈希，全部排除最终指标，不覆盖向量基线 |
| `SHA256SUMS.json` | 证据包文件清单；校验程序失败时退出非零 |

本步仅使用既有锁定依赖和 Python 标准库；摘要实现依据 [Python hashlib 文档](https://docs.python.org/3/library/hashlib.html)。摘要验证内容一致性，不能替代真实调用或人工复核。

### 重现本次无模型检查（仓库根目录，PowerShell）

```powershell
python data/eval/check_eval_set.py
python reports/verify_final_evidence.py --check-working-tree --check-source-archive
cd backend
uv sync --locked
$run = [guid]::NewGuid().ToString('N')
uv run --locked python -m app.evaluate --split test --output "../artifacts/eval/step30-recheck-$run/A"
# 预检退出2表示未执行模型；阅读 manifest.blockers，不能当作通过真实实验。
uv run --locked python -m app.evaluate_rrf --split test --output "../artifacts/eval/step30-recheck-$run/B"
uv run --locked python -m app.evaluate_rerank --split test --output "../artifacts/eval/step30-recheck-$run/C"
uv run --locked python -m app.evaluate_agent --help
```

默认归档校验不需要模型、数据库或源代码大包；`--check-working-tree` 还检查当前业务源码/资料是否匹配快照，`--check-source-archive` 要求本地归档包存在。不同源码版本可先检出上述 commit 到独立目录再核对，不应覆盖当前工作区。重跑输出使用新目录，不覆盖旧记录。

### 未来实际执行条件（本次未执行）

先补齐人工复核、冻结配置记录、授权真实索引/映射和费用上限；模型密钥只放环境变量。A 使用现有 `app.evaluate --split test --run-real --questions <已复核冻结题集> --mapping <映射> --output <新目录>`。B/C 的相同参数只执行检索对照，不能生成完整问答比较；D 目前没有合法的 test CLI 命令。完整四方案最终对比应在这些缺口明确解决并固定新版本后执行，不提供虚假的“一键完整实验”命令。

## 7. 结论

目前可确认输入完整性与预检行为，**不能判断 B 优于 A、rerank 有提升或 Agent 更适合某类题**。沿用现有固定 RAG 默认与重排默认关闭，不基于本次无结果改变选择。真实实验、人工事实/引用复核及真实失败分析仍待完成；本报告和证据包如实记录当前状态。
