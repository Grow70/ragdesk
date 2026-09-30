# 简历描述与证据审查（第 31 步）

核对日期：2026-10-01；材料基线提交 `f147e2f`。项目名建议：**Ragdesk：面向模拟企业资料的知识库问答系统（学习项目）**。下面恰好三条可用描述；数字是工程测试结果或程序上限，不是效果提升、客户规模或 SLA。面试时应能解释对应实现，不能只背描述。

## 可放入简历的三条描述

1. 为模拟企业资料提供按知识库隔离的可追溯问答，基于 FastAPI、PostgreSQL/pgvector 与 React 实现文档上传、后台入库、固定 RAG 和受保护引用；在真实数据库、API 与 worker 配合 fake 模型的浏览器回归中，验证登录至引用闭环、删除不可见和跨库访问拒绝。
2. 为处理索引重建失败和 worker 迟到写入，实现候选构建与 `active_build_id` 原子切换、数据库任务租约和 `run_token` 校验；通过故障注入验证旧执行不能覆盖新结果、重建失败保留旧索引，以及删除与入库竞争不复活文档。
3. 为控制 Agent 的重复检索和调用预算，使用 LangGraph 编排只读搜索/补读工具，程序限制每轮最多 3 次工具调用、6 次模型请求和 60 秒；可控 fake 轨迹验证首次超时后改写查询并再次检索成功，实际该轨迹记录 2 次工具调用、6 次模型请求，并覆盖重复调用、预算耗尽与权限撤销分支。

## 逐项证据审查

| 简历子描述 | 对应代码 | 对应验证/记录 | 审查结论与边界 |
| --- | --- | --- | --- |
| 1：FastAPI、PG/pgvector、React 与上传入库 | [应用入口](../backend/app/main.py)、[文档 API](../backend/app/api/documents.py)、[worker](../backend/app/worker.py)、[前端文档页](../frontend/src/Documents.tsx) | [真实闭环夹具](../backend/tests/test_frontend_keyflow.py)、[浏览器场景](../frontend/tests/keyflow-real.spec.ts)；第 29 步 E2E 4 个 Python 夹具通过 | 保留；真实数据库/浏览器，模型 fake，内层用例不重复计数 |
| 1：固定 RAG 与可追溯引用 | [answer_question / get_source](../backend/app/services/answers.py)、[current_sources](../backend/app/repositories/sources.py)、[问答页](../frontend/src/Question.tsx) | [test_answers.py](../backend/tests/test_answers.py)：`test_answer_citations_are_backend_owned_and_sources_are_protected`；同一浏览器场景核对上传文档 ID 与引用 | 保留“来源可追溯”；不能升级成“事实完全正确” |
| 1：隔离与删除不可见 | [require_kb_member / require_kb_admin](../backend/app/services/knowledge_bases.py)、[有效块查询](../backend/app/repositories/chunks.py) | [test_document_lifecycle.py](../backend/tests/test_document_lifecycle.py)：`test_delete_hides_every_read_and_allows_new_upload`；浏览器跨库 search/source/raw/job 拒绝 | 保留已测场景；不等于通过全面安全审计 |
| 2：原子发布与旧结果隔离 | [ingest_document 发布事务](../backend/app/services/ingest.py)、[LeaseGuard](../backend/app/services/ingestion_jobs.py) | [test_job_recovery.py](../backend/tests/test_job_recovery.py)：`test_old_worker_delayed_result_cannot_overwrite_new_success`，迟到成功/失败两个分支 | 保留；单 worker 恢复设计，不称分布式调度或端到端 exactly-once |
| 2：重建失败与删除竞争 | [文档生命周期](../backend/app/services/documents.py)、[ingest_document](../backend/app/services/ingest.py) | [test_document_lifecycle.py](../backend/tests/test_document_lifecycle.py)：`test_failed_rebuild_and_modified_original_preserve_old_index`、`test_delete_races_with_running_worker_without_resurrection` | 保留；没有上传新文件覆盖旧文档的版本管理功能 |
| 3：LangGraph 只读工具与硬上限 | [run_agent / _compile](../backend/app/agent/loop.py)、[工具](../backend/app/agent/tools.py)、[RoundLimits / RequestBudget](../backend/app/llm/budget.py) | [test_agent_loop.py](../backend/tests/test_agent_loop.py)：`test_limits_cannot_raise_hard_caps`、`test_model_budget_reserves_final_request_and_never_starts_a_seventh` | 3/6/60 是限制值，不是平均调用量或延迟实测 |
| 3：超时后改写、实际 2/6 调用 | [decide / execute / finalize](../backend/app/agent/loop.py) | `test_failed_retrieval_result_drives_rewrite_then_success`；[工程证据快照](../reports/evidence/step31/engineering-validation.json) 最后一项保留原始 fake trace | 保留并明确 fake 固定输出；不能写成真实模型自主改写效果 |
| 3：重复、预算、权限撤销 | [循环与最终校验](../backend/app/agent/loop.py) | 同测试文件的 `test_repeat_is_rejected_before_second_tool_execution`、`test_always_empty_stops_after_three_tools`、`test_revocation_or_deletion_during_calls_never_returns_answer` | 保留回归行为；不宣称模型提示注入完全免疫 |

代码测试在第 29 步后端完整套件中有历史通过记录：364 passed、6 deselected；原始 JUnit/日志已在本步重新核对。最后空密钥环境修改另有 41 项定向通过，不能说“该修改后全量再次通过”。第 31 步只整理材料，没有重新运行这些测试。

[工程证据快照](../reports/evidence/step31/engineering-validation.json) 保存历史日志摘要、来源路径与 SHA-256；原始日志在 Git 忽略的 `artifacts/`。该快照便于交接，不是第三方签名。复现见 [CI 文档](ci.md)；真实效果状态见 [最终报告](../reports/final-evaluation.md)。

## 已删除或降级的候选说法

| 不采用的说法 | 原因 | 允许的表述 |
| --- | --- | --- |
| 服务某企业、支撑万人/高并发 | 无客户或压测证据 | 面向模拟企业资料的学习项目 |
| 混合检索/重排提升准确率 X% | A/B/C/D 真实实验未执行，无人工评分 | 实现独立 BM25、RRF 和可关闭 rerank 适配，待真实评测；未放入主简历成果 |
| 引用保证零幻觉 | 来源合法不能证明支持关系 | 后端校验引用归属和有效性，事实仍需人工审核 |
| Agent 显著优于固定 RAG | 仅 fake 分支验证，默认演示只搜索一次 | 有预算的再次检索编排及可控回归 |
| HNSW 高性能向量索引 | 当前是 pgvector 精确搜索 | 使用 pgvector cosine distance 检索 |
| exactly-once 分布式任务系统 | 单 worker；模型外部副作用可重复 | 租约和 run_token 防旧执行发布 |
| CI 全绿、生产可用 | 远端 Actions 未运行；只有本地证据 | 建立 CI 工作流，本地按套件验证 |
| 30 道正式金标/完成最终实验 | 全部 draft，实际四方案各 n=0 | 30 道待复核草案与未执行状态归档 |

最终三条描述均有代码和验证入口支持；没有填入真实模型质量、费用、延迟改善或生产规模数字。将来只有新增实际证据后才能升级措辞。
