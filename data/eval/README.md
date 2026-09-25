# 第 7B 步评测集草案

[questions.json](questions.json) 含 30 条手工拟定、尚未人工复核的题目。每条 `review_status` 均为 `draft`，不得据此报告正式准确率或真实 RAG 效果；本步没有运行模型、检索或问答。[REVIEW.md](REVIEW.md) 展示逐条勾选清单及证据原文，勾选本身不等于审核通过。

| 类别 | dev | test | 合计 |
| --- | ---: | ---: | ---: |
| 直接事实 `direct_fact` | 6 | 6 | 12 |
| 同义改写 `paraphrase` | 3 | 3 | 6 |
| 跨文档综合 `cross_document` | 3 | 3 | 6 |
| 资料不足 `insufficient_evidence` | 3 | 3 | 6 |

## 字段与来源

- `kb_id` 是 [模拟资料清单](../sample_docs/manifest.json)中的逻辑知识库标签 `A` 或 `B`，尚非数据库 UUID。后续执行前须把标签映射为实际知识库 ID。
- `expected_facts` 是根据资料人工拟定的待核事实，不是模型生成的标准答案；`gold_evidence` 只引用该库文档的稳定样例文档标识、章节和逐字摘录，不使用尚未产生的 `chunk_id`。文档标识也不等于未来上传生成的数据库文档 ID。
- `fact_group` 按主题聚合，`paraphrase_of` 将每道改写题对应到直接题。dev 放 A 库报销/出行/休假及 B 库报销；test 放 A 库产品/接口/运维及 B 库运维。同一事实的直接题与改写题同组、同 split。
- `insufficient_evidence` 的 `expected_facts` 为空；`missing_information` 说明缺什么，`checked_document_ids` 列出该库本次核对的全部样例文档。空证据列表不证明“全部资料都没有该事实”，仍须人工核查当前语料范围。个别记录引用适用范围原文，仅说明现有规则的范围，不能当作未知答案的证据。

## 使用与复核

从仓库根目录执行：

```bash
python3 data/eval/check_eval_set.py
python3 data/eval/check_eval_set.py --list
```

脚本检查必填字段、重复 ID、题数、类别与 split、同组及改写题配对、源文件 SHA-256、证据所属知识库，以及摘录是否逐字存在于指定章节。`REVIEW.md` 首行记录题目文件哈希；修改题目后需用 `python3 data/eval/check_eval_set.py --list > data/eval/REVIEW.md` 重新生成清单并逐条复核。真正完成人工复核后，可将单条记录改为 `review_status: "reviewed"`，同时填写 `reviewed_by` 和 `reviewed_at`；单纯勾选清单不改变状态。脚本无法自动判定问题是否歧义、推理是否正确或资料不足是否成立。

dev 可在未来用于调参。test 的规范化内容摘要记录在 [test.freeze.sha256](test.freeze.sha256)，检查脚本会拒绝意外修改；不能依据 test 的错误反复调参。人工复核发现标注错误时，应记录修订缘由和版本，再有意识地更新冻结摘要；这与看模型结果后调题不同。所有记录保持 `draft`，直到项目负责人逐条确认。
