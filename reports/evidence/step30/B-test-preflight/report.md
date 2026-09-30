# 向量 / BM25 / RRF 三路对比

仅真实模型、已复核且三路成功未降级的有 gold 题进入配对指标分母。
fake 仅验证流程；null 表示未测，不能解读为没有提升。
正/零/负差值逐项报告，不自动推断总体优劣。
单路耗时是同次融合内的观测；RRF 耗时包含两路及授权复核。

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
    "vector": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    },
    "bm25": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    },
    "rrf": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    }
  },
  "rrf_minus": {
    "vector": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    },
    "bm25": {
      "hit_at_5": null,
      "evidence_recall_at_5": null,
      "mrr_at_5": null
    }
  },
  "timings": {
    "vector": {
      "n": 0,
      "mean_ms": null
    },
    "bm25": {
      "n": 0,
      "mean_ms": null
    },
    "rrf": {
      "n": 0,
      "mean_ms": null
    }
  },
  "observed_model_calls": 0,
  "reported_tokens": null,
  "unknown_usage_calls": 0,
  "conclusion": "No verified paired comparison; improvement is unknown.",
  "status": "not_run",
  "mode": "not_run",
  "blockers": [
    "UNREVIEWED_SAMPLES",
    "RUN_NOT_REQUESTED"
  ],
  "split": "test"
}
```
