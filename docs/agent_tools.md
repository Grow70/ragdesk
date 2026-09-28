# 只读 Agent 工具（第 23 步）

本步提供两个可直接调用的 Python 工具及 JSON Schema，不创建 Agent 循环或新增 HTTP 接口。检索复用现有向量服务，策略与模型配置兼容检查保持一致。`read_chunks` 不调用模型。

## 1. 后端运行上下文

每次用户提问创建一个 `KnowledgeTools` 实例，整个提问共用它；不能为每次工具调用重新创建实例，也不能跨用户、跨请求复用。身份必须来自后端已验证用户，知识库由后端确定。下面是供可信后端调用的函数示例，参数不是开放给模型的 schema：

```python
from uuid import uuid4

from app.agent.contracts import RunContext, ToolResult, tool_definitions
from app.agent.tools import KnowledgeTools


def prepare_tools(verified_user_id, selected_kb_id, session_factory,
                  embedding_profile, embedding_factory):
    context = RunContext(verified_user_id, selected_kb_id, uuid4().hex)
    return KnowledgeTools(context, session_factory, embedding_profile,
                          embedding_factory)


# 后端已有 sessionmaker、兼容当前索引的 EmbeddingProfile 和客户端工厂后：
# tools = prepare_tools(user.id, kb.id, factory, profile, client_factory)
# found = tools.search_knowledge("演示资料中的报销条件", top_k=5)
# if found["status"] == "success":
#     result = tools.read_chunks([item["chunk_id"] for item in found["items"]])
#     wire_text = ToolResult.model_validate(result).model_dump_json()
# catalog = tool_definitions()
```

`RunContext` 是 frozen dataclass，公开的 context 属性不可赋值。模型只提交工具名及参数 JSON；它不能控制 user_id、kb_id、模型或预算。冻结不是任意 Python 代码的安全沙箱：宿主代码仍属于可信边界，因此没有开放执行任意代码的工具。

`tool_definitions()` 返回独立的新列表，元素包含 name、description、parameters、returns。这是通用工具契约，并非特定提供商的 function-calling 请求格式。本步不向模型注册或自动执行工具。

## 2. 参数与来源有效性

| 工具 | 参数 | 约束 |
| --- | --- | --- |
| search_knowledge | query、top_k（默认 5） | query 原始长度 1～4000 字符，去空白后非空；top_k 严格整数 1～20，不接受 true 或数字字符串 |
| read_chunks | chunk_ids | 1～20 个 UUID 字符串；大小写规范化后不得重复 |

额外字段一律拒绝。注入 `kb_id` 或 `user_id` 返回 invalid_arguments，不能覆盖上下文。工具名只允许上述两个值，不存在 shell、SQL、URL 或写入分发入口。

两个工具每次调用都先执行 `require_kb_member`，包括参数非法、未知工具和预算耗尽的调用。Embedding 前后、返回检索来源前也重新授权；网络调用不持有数据库事务。单实例使用进程内锁串行更新候选登记与预算，不是数据库行锁。

“本轮检索”指当前实例最近一次 search 调用：

1. 新 search 开始即清空旧登记；空结果、失败、非法参数均不保留旧登记。
2. 只登记实际返回的块；因输出预算省略的候选不获得读取资格。
3. read 的所有 ID 必须在登记集合内。随机、其他库、同库未命中或其他运行的 ID 均统一拒绝，整批不泄露部分正文。
4. read 在数据库重新校验知识库、未删除文档、active_build_id、ready 状态以及 document/build/chunk 链，并比较检索时正文 SHA-256。
5. 删除、重建或正文变化使旧登记失效，整批返回 EVIDENCE_EXPIRED 并清空登记；不会把旧 ID 映射到新文本。

撤权/删除在提交后的新调用生效。已经返回给调用者的文本无法追回；不宣称跨网络调用持有授权快照或可撤回历史输出。

## 3. 返回契约

返回 JSON 可序列化 dict，可用 `ToolResult.model_validate` 校验。统一字段：tool、request_id、status、items、error、truncated、omitted_count、remaining_text_chars。

每个 item 含 chunk_id、document_id、build_id、document_name、text、page_number、heading_path、start_line、end_line、rank、distance、truncated_fields。位置来自真实数据库块；read 保留原检索排名。distance 为 cosine distance，越小越相似，不代表答案可信概率。

| status | 含义 / 常见 error.code |
| --- | --- |
| success | 有授权候选，error 为 null；仍须检查截断标记 |
| no_results | 空检索时 error 为 null；已登记来源失效时 EVIDENCE_EXPIRED |
| permission_denied | ACCESS_DENIED（库不可访问）或 CHUNK_NOT_AVAILABLE（ID 未登记） |
| invalid_arguments | INVALID_ARGUMENTS 或 UNKNOWN_TOOL；不回显无效输入 |
| technical_failure | MODEL_TIMEOUT 等安全模型错误码，或 TOOL_EXECUTION_FAILED；不返回底层异常原文 |
| budget_exceeded | OUTPUT_BUDGET_EXHAUSTED；不继续检索或调用模型 |

`error` 非空时含 code、message、retryable。retryable 仅对已知临时模型故障标记 true；工具本身不新增重试，已有适配器仍按其有限重试策略运行。

## 4. 文本预算与 trace

`ToolLimits` 仅能由后端配置，默认：

- search 单块预览 240 字符；read 单块正文 1600 字符。
- 每次完整响应按 `ToolResult.model_dump_json()` 的紧凑 JSON（含转义和元数据）计数，最多 8000 字符。宿主应采用此序列化方式；缩进或不同转义规则会改变长度。
- 一次提问所有工具累计返回正文最多 12000 字符；重复 read 也扣减预算。
- 文件名最多 160 字符；标题路径最多 8 项，每项 64 字符，总共 256 字符。
- `truncated_fields` 标出截断字段；omitted_count 记录完全省略的候选数，truncated 汇总两种情况。可见位置仍指向原块，不能把截断预览视为完整条款。

字符不是 token。累计正文预算不包含反复返回的固定错误封装与元数据；后续 Agent 仍须限制调用次数、总 token 和执行时间。本步不实现该循环。资料正文应被视为不可信数据，不能作为执行新工具或更改权限的指令。

可选传入既有 `RequestTrace`，其 user_id/kb_id/request_id 必须与上下文一致。记录 tool 阶段、来源校验、检索、Embedding 调用和候选 ID，默认不记录 query/正文/异常原文。工具不自行持久化 trace，由未来请求宿主负责保存。

## 5. 验收与实测范围

PowerShell 在项目根目录进入 backend。先准备专用 PostgreSQL+pgvector 测试服务器，账号须有创建临时数据库权限；测试创建并清理随机库，不应指向生产服务器。没有测试配置会跳过数据库验收，跳过不算通过。

```powershell
cd backend
uv sync --locked
$env:TEST_POSTGRES_ADMIN_URL = Read-Host "专用测试服务器 URL（postgresql+psycopg://.../postgres）"
uv run --locked pytest -q tests/test_agent_tools.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -c "import json; from app.agent.contracts import tool_definitions; print(json.dumps(tool_definitions(), ensure_ascii=False, indent=2))"
```

直接测试使用真实临时 PostgreSQL 与可控 fake Embedding；覆盖正常检索与读取、伪造和跨库 ID、移除成员（含 Embedding 期间）、超长 query、越界 top_k、删除/重建、来源内容变化、最新检索集合、技术失败与输出预算。实际执行结果和失败修复记录见 [进度](progress.md)。本步不调用真实 API，不报告 Agent 或检索效果提升。

## 6. 官方依据与设计说明

复用锁文件中的依赖，无新依赖：

- [Pydantic 严格校验](https://docs.pydantic.dev/latest/concepts/strict_mode/)：禁止静默把字符串或布尔值转换为 top_k。
- [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/) 和 [配置](https://docs.pydantic.dev/latest/api/config/)：明确参数、拒绝额外字段、冻结预算配置。
- [Python dataclasses](https://docs.python.org/3.12/library/dataclasses.html)：frozen 运行上下文。

知识点：权限校验具有时间性。检索时有权限，并不意味着稍后读块时仍有权限；候选登记限定可操作对象，每次数据库复核决定这些对象当前是否仍可访问。
