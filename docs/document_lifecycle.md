# 文档删除与同一原文件重建（第 21 步）

## 接口与可见性

以下接口沿用后端 JWT 身份及 `require_kb_admin`。普通成员返回 403；没有知识库权限、不存在或跨库拼接的对象统一 404。

| 操作 | 接口 | 结果 |
| --- | --- | --- |
| 删除 | `DELETE /knowledge-bases/{kb_id}/documents/{document_id}` | 200，含 `document_id/status/cleanup_status/request_id`；status 为 deleted |
| 重建 | `POST /knowledge-bases/{kb_id}/documents/{document_id}/rebuild` | 202，含 `job_id/job_status/status_url`；无请求体 |
| 任务查询 | `GET /knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job_id}` | 管理员读取状态及安全错误码；删除后 404 |
| 引用 | `GET /knowledge-bases/{kb_id}/sources/{document_id}/{build_id}/{chunk_id}` | 成员读取当前来源；真实旧 ready 构建返回 410 SOURCE_EXPIRED |

`/rebuild` 与现有 `/ingestions` 共用服务。一个文档最多一个活动任务，重复请求返回已有任务；新的 build_id 在 worker 领取后产生，与 document_id、job_id 不同。202 的 `status=uploaded` 沿用原响应，表示原文件已保存；实际构建状态看 job_status 和任务查询。

上传相同文件名但不同内容会创建另一个文档，不会覆盖已有 document_id。重建只读取数据库保存的 storage_key，并再次校验原文件 SHA-256；后台文件被替换会以 DOCUMENT_HASH_MISMATCH 失败。

## 删除与竞争

1. 管理员校验后，入队/删除使用同一个按文档 UUID 派生的事务咨询锁，防止扫描任务后又排入新任务。
2. 删除按任务→文档→构建加行锁。在一个短事务内设置 deleted_at、清空 active_build_id，将 queued/running 任务改为 failed，错误码 DOCUMENT_DELETED（代表取消），清空 run_token 和租约，并将 processing 构建标记失败。
3. worker 的写入和发布仍检查当前 token、running 状态、有效租约和文档未删除。删除先提交时，延迟返回的 worker 失去写入权；发布先提交时，随后删除清空其有效指针。外部模型请求无法撤回，仍可能计费。
4. 提交后再做原文件清理。新发起的向量、BM25、引用、文档列表/详情、原文件及任务查询均排除已删除文档。历史构建和块保留，但不会通过正常读取返回。

删除提交是对外不可见的生效点。数据库提交失败则删除标记、任务取消全部回滚，而且不会先删除文件。已经在删除前开始的请求不承诺撤回已发送给客户端的内容。

## 文件清理边界

仅接受服务器生成的 `objects/<32位十六进制>` 键，不接受用户文件名或任意路径、不递归删除。支持目录句柄的平台以 `dir_fd + O_NOFOLLOW` 打开受控目录，拒绝目录/文件符号链接，仅解除该目录内普通文件的链接。

| cleanup_status | 含义 | 后续处理 |
| --- | --- | --- |
| removed | 原文件已清理 | 无需重试 |
| missing | 原文件原本就不存在 | 删除仍成功 |
| blocked | 非法键、符号链接或目录结构不符 | 管理员检查存储配置；接口不跟随链接 |
| pending | 文件系统暂不可用，或平台不支持安全目录句柄 | 私有文件保留，逻辑删除仍成功；具备清理条件后重复 DELETE |

**当前 Windows 原生运行会采用 pending 分支，不执行物理删除；本次真实运行验证在 Linux/WSL，Windows 分支仅做可控能力缺失测试。** 不进行宽松的路径清理回退。数据库提交后进程退出也可能留下私有文件；尚未实现自动垃圾回收。重复 DELETE 对同库管理员幂等，不会恢复文档。

## 重建、模型配置与引用

- 重建先写新的候选构建；旧 ready 构建在排队、构建中及失败后继续服务。
- 全部块/向量验证通过后，在同一短事务内将新构建设为 ready、切换 active_build_id、将任务设为 succeeded。
- 已有有效构建的 provider/model/dimensions/config_version 必须匹配，否则入队或 CLI 返回 INCOMPATIBLE_REBUILD_CONFIG，HTTP 为 409。切块参数允许变化。当前向量列固定 1536 维，非法服务配置为 503 INVALID_EMBEDDING_CONFIG。
- 模型或维度迁移需要单独的数据库迁移、全库重建与查询配置切换设计，本步拒绝直接切换，不把旧向量伪装为新模型向量。查询仍需使用与有效索引相同的配置。
- 旧引用对当前成员返回 410 SOURCE_EXPIRED，不附带原文，也不按相同片段序号映射新文本。删除、未授权、未知/伪造 ID 链及未发布构建均返回 404。要获取新证据需重新提问/检索。

## PowerShell 操作示例

先按 README 配好 API、数据库与模型后启动唯一 worker；已有终端运行 worker 时跳过此命令：

```powershell
cd backend
uv run --locked python -m app.worker
```

在另一个终端，用演示知识库的管理员令牌及文档 ID 验收。令牌通过输入读取，不写入脚本文件：

```powershell
$base = "http://127.0.0.1:8000"
$token = Read-Host "管理员 Bearer token"
$headers = @{ Authorization = "Bearer $token" }
$kbId = Read-Host "演示知识库 ID"
$documentId = Read-Host "已上传的演示文档 ID"
$documentUrl = "$base/knowledge-bases/$kbId/documents/$documentId"

$job = Invoke-RestMethod -Method Post -Uri "$documentUrl/rebuild" -Headers $headers
$job
Invoke-RestMethod -Uri "$base$($job.status_url)" -Headers $headers
# 任务成功前仍应检索到旧 build_id，成功后检索结果只使用新 build_id。
Invoke-RestMethod -Method Post -Uri "$base/knowledge-bases/$kbId/search" `
  -Headers $headers -ContentType "application/json" -Body '{"query":"报销","top_k":5}'

# 删除所选演示文档；响应同时显示文件清理结果。
Invoke-RestMethod -Method Delete -Uri $documentUrl -Headers $headers
# 再次检索应没有该 document_id；原引用/原文件 GET 应为 404。
Invoke-RestMethod -Method Post -Uri "$base/knowledge-bases/$kbId/search" `
  -Headers $headers -ContentType "application/json" -Body '{"query":"报销","top_k":5}'
```

离线自动验收，在 backend 目录执行。TEST_POSTGRES_ADMIN_URL 必须指向可创建/删除临时库的测试 PostgreSQL/pgvector 服务器；测试自行生成随机库名，不对已有用户数据回退迁移。未配置时数据库测试会跳过，不能算通过：

```powershell
$env:TEST_POSTGRES_ADMIN_URL = Read-Host "临时服务器 psycopg URL（管理员连接）"
uv run --locked pytest -q tests/test_document_lifecycle.py
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

固定测试使用 fake Embedding 和 Event 同步，覆盖删除/查询隔离、取消排队、Embedding 后及发布前删除、删除/入队两种顺序、提交失败回滚、成功切换及旧引用、失败重建、文件哈希被改、模型/维度不兼容和受控清理。它们验证权限及一致性，不代表真实语义检索质量。

官方依据：[PostgreSQL 17 锁与事务咨询锁](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)、[Python 3.12 文件操作与 dir_fd](https://docs.python.org/3.12/library/os.html#os.unlink)。未增加依赖，沿用现有 uv.lock。
