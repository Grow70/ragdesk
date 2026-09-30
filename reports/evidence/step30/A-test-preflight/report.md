# 向量检索基线

运行状态：not_run；split：test；样本量：15。

效果值为 null 表示没有可报告的测量，不能当成 0。技术错误不算拒答。
Evidence Recall@5 为逐题宏平均；严格匹配完整原文及位置。
引用支持结论和事实正确性待独立人工复核；小样本不代表总体效果。

```json
{
  "n": 15,
  "retrieval": {
    "n": 0,
    "hit_at_5": null,
    "evidence_recall_at_5": null,
    "mrr_at_5": null,
    "gold_units": 0,
    "matched_units": 0
  },
  "answerable": {
    "n": 12,
    "completed_n": 0,
    "answered": 0,
    "insufficient_evidence": 0,
    "needs_clarification": 0,
    "errors": 0,
    "not_run": 12,
    "refusal_rate": null
  },
  "unanswerable": {
    "n": 3,
    "completed_n": 0,
    "answered": 0,
    "insufficient_evidence": 0,
    "needs_clarification": 0,
    "errors": 0,
    "not_run": 3,
    "refusal_rate": null
  },
  "citations": {
    "n": 0,
    "valid_n": 0,
    "valid_rate": null
  },
  "timing": {
    "retrieval_ms_n": 0,
    "retrieval_ms_mean": null,
    "total_ms_n": 0,
    "total_ms_mean": null
  },
  "tokens": {
    "known_reported_total": null,
    "unknown_usage_calls": 0,
    "total_tokens": null,
    "note": "Reported tokens only; failure/retry usage may be unknown."
  },
  "manual_review": "pending: factual correctness and citation support are not automatically scored",
  "run_status": "not_run",
  "execution_mode": "not_run",
  "blockers": [
    "UNREVIEWED_SAMPLES",
    "REAL_RUN_NOT_REQUESTED"
  ],
  "split": "test"
}
```
