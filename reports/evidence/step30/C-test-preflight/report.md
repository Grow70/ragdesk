# RRF / RRF+rerank 配对比较

仅真实模型、已复核且两组成功未降级的有 gold 题进入配对指标分母。
fake 仅验证流程；null 表示未测，不能解读为没有提升。
正/零/负差值逐项报告，不自动推断总体优劣。
共用 RRF20 基线；总耗时包含重排及授权复核，增量耗时单列。
计费 search_units 不等于金额。

```json
{
  "n": 15,
  "paired_n": 0,
  "excluded_n": 15,
  "completed_n": 0,
  "error_n": 0,
  "not_run_n": 15,
  "degraded_n": 0,
  "methods": {
    "rrf": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    },
    "rrf_rerank": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    }
  },
  "rerank_minus_rrf": {
    "hit_at_5": null,
    "evidence_recall_at_5": null,
    "mrr_at_5": null
  },
  "timings": {
    "rrf": {
      "n": 0,
      "mean_ms": null
    },
    "rerank_increment": {
      "n": 0,
      "mean_ms": null
    },
    "rrf_rerank": {
      "n": 0,
      "mean_ms": null
    }
  },
  "observed_model_calls": 0,
  "reported_embedding_tokens": null,
  "embedding_unknown_usage_calls": 0,
  "rerank_calls": 0,
  "reported_search_units": null,
  "rerank_unknown_usage_calls": 0,
  "currency_cost": null,
  "default_enabled": false,
  "conclusion": "Real comparison unverified; disabled by default.",
  "status": "not_run",
  "mode": "not_run",
  "blockers": [
    "UNREVIEWED_SAMPLES",
    "RUN_NOT_REQUESTED"
  ],
  "split": "test"
}
```
