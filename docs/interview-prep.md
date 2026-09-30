# 面试准备：实现、验证与局限（第 31 步）

以下回答针对当前代码。测试名表示可执行验证入口；历史通过范围见 [CI 记录](ci.md)、[进度](progress.md)和[工程证据摘要](../reports/evidence/step31/engineering-validation.json)，不是第 31 步重新跑过。真实模型效果统一以[最终报告](../reports/final-evaluation.md)为准：未执行。

## 1. 为什么按字符切块？overlap 有什么代价？

**简答**：默认 600 字符、重叠 80 字符，可配置且拒绝 overlap ≥ chunk_size。先保持标题/页的分组，再优先段落与句子边界，超长内容硬切；同一输入结果稳定。重叠让边界事实更可能完整进入候选，也增加存储、Embedding 成本和重复上下文。PDF 不跨物理页拼接。

- **代码**：[chunking.py](../backend/app/chunking.py)：`ChunkConfig`、`_split_end`、`chunk_sections`；[PDF 解析](../backend/app/parsers/pdf.py)。
- **验证**：[test_chunker.py](../backend/tests/test_chunker.py)：`test_chinese_sentence_boundary_is_preferred_before_hard_cut`、`test_pdf_pages_never_merge_and_keep_physical_locator`、`test_code_block_stays_separate_and_preserves_markers`、`test_invalid_config_is_rejected`。
- **局限**：字符数不等于 token 数；600/80 是工程默认，没有真实 dev 质量实验支持其最优性；长代码块仍可能被切断，不恢复复杂表格。

## 2. Embedding 为什么必须保存配置？维度相同就兼容吗？

**简答**：模型不同或编码约定变化会改变向量空间；维度相同仍可能不兼容。构建保存模型/配置标识、维度和切块参数，查询限定匹配配置。当前迁移使用 vector(1536)，Chat 与 Embedding 分开配置；适配器检查数量、维度、有限值、零向量，认证/参数/维度错误不重试。

- **代码**：[EmbeddingProfile / ensure_rebuild_compatible](../backend/app/services/ingest.py)、[OpenAIEmbeddingClient](../backend/app/llm/openai.py)、[0003_ingest_vectors.py](../backend/alembic/versions/0003_ingest_vectors.py)、[模型配置文档](model_config.md)。
- **验证**：[test_llm.py](../backend/tests/test_llm.py)：`test_bad_embedding_output_is_not_retried`、`test_only_transient_errors_retry_up_to_three_attempts`；[test_document_lifecycle.py](../backend/tests/test_document_lifecycle.py)：`test_rebuild_rejects_model_and_dimension_change_before_queue_or_call`。
- **局限**：1536 是当前契约维度，真实 API 未验证。配置标签不是向量语义兼容的数学证明；变更需要新构建/迁移和检查，不能只改标签。

## 3. BM25 怎么保留错误码？为什么不能过滤所有非正分数？

**简答**：query/document 都做 NFKC、casefold；正则保留英文、数字和带 `-_.` 的型号，中文用 jieba 且 HMM=False。先读取当前用户有权且 active/ready 的块，每请求建 BM25Okapi 集合；先检查共同词项，无交集才排除。库和词频条件可能产生零或负分，不能用 score>0 判断相关性。

- **代码**：[tokenize / rank_chunks](../backend/app/retrieval/bm25.py)、[BM25 service](../backend/app/services/bm25.py)、[lexical_chunks_stmt](../backend/app/repositories/chunks.py)。
- **验证**：[test_bm25.py](../backend/tests/test_bm25.py)：`test_fixed_tokenization_preserves_identifiers_numbers_and_case`、`test_negative_and_zero_scores_do_not_remove_actual_matches`、`test_synonyms_are_not_expanded`、`test_no_cache_after_delete_update_or_member_removal`。
- **局限**：词面匹配没有同义词扩展；每次分词/建集合有成本。代码记录 tokenize/index/score 耗时，但没有真实规模吞吐量或语义质量结论。

## 4. 为什么用 RRF，不直接相加两个分数？

**简答**：cosine distance 越小越相似，BM25 分数越大越靠前，两者尺度不同。两路最多各 20 条，按 chunk_id 去重，使用 `Σ 1/(60+rank)`，rank 从 1 开始；缺失一路贡献 0。保留原始排名，同分按 chunk_id 字符串排序，内部用 Fraction 稳定比较。例如双路第一是 `2/61`，仅单路第一是 `1/61`，这是公式示例而非实验结果。

- **代码**：[fuse / _unique](../backend/app/retrieval/rrf.py)、[hybrid service](../backend/app/services/hybrid.py)。
- **验证**：[test_rrf.py](../backend/tests/test_rrf.py)：`test_hand_computed_fusion_dedup_and_raw_ranks`、`test_empty_route_stable_ties_and_candidate_cap`、`test_non_transient_failures_never_degrade`。
- **局限**：RRF 不知道相关性的绝对强度，k=60 没有本项目效果最优性证明；授权错误不能降级绕过。默认问答尚未接入混合服务。

## 5. 用了什么向量索引？为什么没有独立搜索集群？

**简答**：当前用 pgvector cosine distance 精确搜索，按距离升序返回，尚未建 HNSW/IVFFlat。元数据、权限和向量放同一 PostgreSQL，便于在同一个查询里限定知识库、删除标记、active_build_id 和模型配置，也降低演示部署复杂度。ANN 是后续规模驱动的取舍，需要测召回与延迟。

- **代码**：[cosine_search](../backend/app/retrieval/vector.py)、[有效构建过滤](../backend/app/repositories/chunks.py)、[迁移目录](../backend/alembic/versions)。
- **验证**：[test_retrieval.py](../backend/tests/test_retrieval.py)：`test_cosine_order_scope_and_limits` 使用手算小向量测试排序和隔离；[最终报告](../reports/final-evaluation.md)没有真实性能数字。
- **局限**：精确搜索成本随候选规模增长，未测生产规模；“精确”仅指当前向量空间的近邻排序，不代表能召回所有正确语义证据。距离不是答案正确概率。

## 6. 引用是真的，答案就一定正确吗？

**简答**：不一定。模型只输出请求内 citation_ids，后端验证编号属于授权候选，并补齐真实文档、页码/标题和片段；成功回答无引用或引用伪造会被拒绝。合法引用仍可能与结论无关，必须单独做事实与支持关系审核。空检索直接不足；超时/非法 JSON 返回技术错误，不伪装成拒答。

- **代码**：[build_context / _validate_draft / answer_from_chunks / get_source](../backend/app/services/answers.py)、[current_sources](../backend/app/repositories/sources.py)。
- **验证**：[test_answers.py](../backend/tests/test_answers.py)：`test_missing_forged_or_duplicate_citations_are_rejected`、`test_model_cannot_supply_citation_metadata`；[test_agent_evaluation.py](../backend/tests/test_agent_evaluation.py)：`test_malicious_factual_answer_with_valid_id_is_not_semantically_certified`。
- **局限**：结构与来源校验无法自动证明语义蕴含，也不能保证模型会正确识别资料冲突。尚无真实模型事实有据率。

## 7. 只在路由检查权限够吗？成员被移除怎么办？

**简答**：用户从已验签 JWT 得到，模型参数不接受 user_id/kb_id。服务复用 require_kb_member/admin，查询同时限定资源范围。Agent 每次工具、引用查看以及最终返回都重新检查权限和有效构建。已提交的成员移除影响下一请求；无权限与不存在的库统一 404，已授权普通成员的管理操作 403。

- **代码**：[get_current_user](../backend/app/api/dependencies.py)、[权限服务](../backend/app/services/knowledge_bases.py)、[RunContext](../backend/app/agent/contracts.py)、[工具](../backend/app/agent/tools.py)。
- **验证**：[test_knowledge_bases.py](../backend/tests/test_knowledge_bases.py)：`test_two_bases_isolate_users_and_removal_takes_effect`；[test_agent_tools.py](../backend/tests/test_agent_tools.py)：`test_removed_member_denied_on_all_calls_even_invalid_arguments`、`test_revocation_during_embedding_never_returns_text`；[真实浏览器跨库用例](../frontend/tests/keyflow-real.spec.ts)。
- **局限**：检查只能约束服务端访问与新响应，不能收回用户此前已经看到/下载的内容。没有 PostgreSQL RLS、SSO 或第三方安全认证的成果声明。

## 8. 为什么模型调用不放在事务里？如何实现重建原子生效？

**简答**：外部调用不可控，长事务会占连接和锁。入库把解析、Embedding 放在长事务外，分批写入不可检索的候选构建；最后短事务检查完整性、配置、删除状态和执行资格，再把 build 标为 ready、切换 active_build_id。查询仅用当前构建，失败保留旧版。

- **代码**：[ingest_document](../backend/app/services/ingest.py)：`before_publish` 后的 `factory.begin()`；[searchable_chunks_stmt](../backend/app/repositories/chunks.py)；[数据约束](../backend/app/models.py)。
- **验证**：[test_ingest.py](../backend/tests/test_ingest.py)；[test_document_lifecycle.py](../backend/tests/test_document_lifecycle.py)：`test_rebuild_keeps_old_until_success_then_expires_exact_source`、`test_failed_rebuild_and_modified_original_preserve_old_index`。
- **局限**：旧构建引用变成明确失效，不映射到新文本；当前仅同一原文件重建。数据库事务不能原子覆盖外部模型收费与本地文件操作。

## 9. 有租约为什么还要 run_token？幂等是否等于 exactly-once？

**简答**：旧 worker 可能超时后仍返回，新领取任务会生成不同 run_token。发布同一事务内锁任务、检查 running/租约/token，再更新构建和任务结果；旧执行即使带着成功向量也不能发布。唯一约束和构建身份防重复有效块，但崩溃可能导致外部模型请求再次发送。

- **代码**：[claim_next / LeaseGuard / execute_claim](../backend/app/services/ingestion_jobs.py)、[发布事务](../backend/app/services/ingest.py)、[0005_job_leases.py](../backend/alembic/versions/0005_job_leases.py)。
- **验证**：[test_job_recovery.py](../backend/tests/test_job_recovery.py)：`test_old_worker_delayed_result_cannot_overwrite_new_success`、`test_crashes_rebuild_without_duplicate_effective_chunks`、`test_crash_retry_budget_exhausted_without_fourth_claim`，使用可控时钟和 Event。
- **局限**：单 worker 部署，最多三次尝试；不能称为经过分布式规模验证的任务平台。幂等有效发布不等于模型调用、费用和其他外部副作用端到端 exactly-once。

## 10. Agent 怎么保证终止？隐藏重试算不算？

**简答**：LangGraph 根据工具中间结果再次进入决策节点；程序限制三次工具、六次模型请求（决策/Embedding/重试/最终生成均计）、60 秒与 24000 字符累计工具上下文。RequestBudget 每次请求前计数，预留最终生成名额；网络超时随剩余时间缩小。规范化参数指纹阻止等价重复调用，返回前再查权限与来源。

- **代码**：[LoopState / decide / execute / finalize / run_agent](../backend/app/agent/loop.py)、[RequestBudget](../backend/app/llm/budget.py)、[模型 HTTP 调用](../backend/app/llm/openai.py)。
- **验证**：[test_agent_loop.py](../backend/tests/test_agent_loop.py)：`test_failed_retrieval_result_drives_rewrite_then_success`、`test_real_http_retries_share_six_requests_and_timeouts_shrink`、`test_repeat_is_rejected_before_second_tool_execution`、`test_deadline_uses_controlled_clock_and_stops_next_model`、`test_always_empty_stops_after_three_tools`。
- **局限**：上述带 real_http 名称的用例也是 HTTP 协议模拟，不是付费真实 API 验证。超时结束无法保证远端已接收的请求不收费；预算停止也不能保证问题已解决。默认 Compose fake 决策仍只有一次搜索。

## 11. 恶意资料要求执行命令，工具层如何处理？

**简答**：企业资料作为不可信 evidence，系统提示不执行其中指令；工具名单只有 search_knowledge、read_chunks，参数 schema 禁额外身份字段，后者只能读本次许可且仍有效的块。后端拒绝非法工具/参数，无 shell、任意 SQL、URL 或写入工具。

- **代码**：[agent/tools.py](../backend/app/agent/tools.py)、[decision.py](../backend/app/agent/decision.py)、[answers.py](../backend/app/services/answers.py) 的提示与引用校验。
- **验证**：[test_agent_evaluation.py](../backend/tests/test_agent_evaluation.py)：`test_loop_rejects_forged_arguments_before_tool`、`test_loop_blocks_foreign_chunk_even_after_authorized_search`、`test_malicious_evidence_cannot_create_a_new_tool`；[test_answers.py](../backend/tests/test_answers.py)：`test_untrusted_instructions_are_data_and_conflict_has_both_sources`。
- **局限**：工具白名单限制能力，但提示词不能证明真实模型免疫注入；模型仍可能在合法引用下输出错误事实。需要真实对抗样本和人工审核。

## 12. 怎么避免评测泄漏？Hit@5、Recall@5 和 MRR@5 差在哪？

**简答**：dev 调参，test 冻结；同一事实的直接/改写题放同组，gold 用原文及位置，不仅文件名。Hit 看是否至少命中一条，Recall 看证据单元覆盖比例，MRR 是首个相关排名倒数；无 gold 的题不进检索分母。答案事实和引用支持另做人审。用 test 错误修改实现后要标记已用于开发并另建独立测试集。

- **代码**：[load_dataset](../backend/app/evaluation/data.py)、[retrieval_metrics / summarize](../backend/app/evaluation/metrics.py)、[人审指标](../backend/app/evaluation/agent_metrics.py)、[check_eval_set.py](../data/eval/check_eval_set.py)。
- **验证**：[test_evaluation.py](../backend/tests/test_evaluation.py)：`test_rank_recall_and_duplicate_units`、`test_no_gold_no_hit_and_only_first_five`、`test_review_metadata_and_test_freeze_gate`；[最终报告](../reports/final-evaluation.md)与[归档校验器](../reports/verify_final_evidence.py)。
- **局限**：30 条全部 draft，test 15 条未完成人工复核，真实执行 n=0。哈希证明保存内容一致，不能独立证明此前从未看过 test。没有可报告的效果提升。

## 13. 怎样解释延迟、usage 和成本？

**简答**：用 request_id 关联请求，拆分检索/模型/工具阶段；汇总必须注明样本量，端到端耗时不能直接用阶段相加代替。未知 usage 或价格记未知；有有效价格和日期才估成本。重试也计调用，失败请求可能未知用量。30% 到 40% 是增加 10 个百分点，相对增加约 33.3%，这仅是算术示例。

- **代码**：[traces service](../backend/app/services/traces.py)、[resources / distribution](../backend/app/evaluation/agent_metrics.py)、[trace 说明](traces.md)。
- **验证**：[test_traces.py](../backend/tests/test_traces.py)；[test_agent_evaluation.py](../backend/tests/test_agent_evaluation.py)：`test_cost_preserves_unknown_retry_usage_and_price_dates`、`test_fake_or_pending_cannot_report_quality_and_percentiles_have_n`。
- **局限**：历史 CI 总耗时和 fake 流程耗时不是模型响应性能；真实最终实验未运行，没有 P50/P95、费用或降本结论。

## 14. 什么问题值得用 Agent？现在能下结论吗？

**简答**：作为设计判断，清晰单次检索即可获得证据的问题优先固定 RAG，路径易理解、调用预算较小；需要根据缺失证据改写、分步搜索或补读的问题值得在 dev 上比较 Agent。是否值得启用，应看同资料、模型、检索配置和人工评分下的质量收益是否抵偿额外调用与延迟。

- **代码**：[固定 answer_question](../backend/app/services/answers.py)、[Agent run_agent](../backend/app/agent/loop.py)、[配对评测实现](../backend/app/evaluation/agent_comparison.py)。
- **验证**：[test_agent_evaluation.py](../backend/tests/test_agent_evaluation.py)：`test_unpaired_or_invalidated_run_cannot_bias_comparison`；[最终实验报告](../reports/final-evaluation.md)。
- **局限**：这只是待检验的选择原则。本项目没有真实提升证据；D 评测入口目前仅 dev，B/C 只检索，不具备“一键四路完整 test 问答对比”的成果。

## 面试现场最小核验

先按 [CI 文档](ci.md) 创建临时 PostgreSQL+pgvector、安装锁定依赖；在 `backend` 执行以下目标回归（模型 fake，无付费 API）：

```powershell
uv run --locked pytest --ci-suite backend -q tests/test_chunker.py tests/test_bm25.py tests/test_rrf.py tests/test_answers.py tests/test_job_recovery.py tests/test_agent_loop.py
```

无需数据库的归档检查，在仓库根目录：

```powershell
python data/eval/check_eval_set.py
python reports/verify_final_evidence.py --check-working-tree
```

这些命令可以验证代码行为和归档完整性；题集字段/证据检查通过不等于人工金标复核通过。真实模型实验须满足最终报告列出的前提，单独申请预算并运行。
