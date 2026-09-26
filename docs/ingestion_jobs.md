# 第 20A 步：数据库任务与单 worker

## 状态与一致性

上传请求仍需收完文件、检查格式/大小并保存原文件，但不解析、切块或调用 Embedding。新文档及 queued 任务同一数据库事务提交后返回 202；入队失败则回滚本次文档与任务，清理本次临时/最终文件。重复上传不会无限排队：复用活动任务，无活动任务时复用最近终态任务。显式入库请求则可在终态后创建新任务。

`ingestion_jobs` 保存文档、发起用户、构建、状态、尝试数、无密钥的模型/切块/批次配置快照、安全错误摘要和时间字段。数据库部分唯一索引限制每份文档只有一个 queued/running 任务；创建事务锁文档行，串行处理同文档请求，数据库约束兜底。

| 任务状态 | 含义 | attempts |
| --- | --- | --- |
| queued | 已接受，等待 worker | 0 |
| running | 领取事务已提交，正在入库 | 1 |
| succeeded | 现有入库服务确认完整成功并发布 | 1 |
| failed | 正常处理遇到解析、模型、配置等错误 | 1 |

本步不自动重试。模型客户端原有的最多 3 次网络尝试与任务 `attempts` 不同；任务尝试数统计被 worker 领取的次数。

构建仍由原入库服务创建，worker 用 job UUID 作为本次构建 UUID；入库结束后关联存在的 build。排队/运行中 `build_id` 可以为空，缺密钥等创建构建前失败也为空。非空 build_id 有同文档组合外键。`active_build_id` 仍是唯一发布指针，失败重建保留旧有效构建；文档状态可为 ready，而最近重建任务为 failed。

领取、批次写库和任务终态各用短事务；模型调用期间不占数据库连接和行锁。任务失败摘要只使用安全机器码，例如 `OPENAI_API_KEY_REQUIRED`、`MODEL_TIMEOUT`、`EMPTY_BODY`、`INGESTION_JOB_FAILED`，不保存供应商原始报错、正文或密钥。

## 接口与权限

所有接口使用后端 JWT 用户及现有 `require_kb_admin`。处理任务状态沿用原架构的管理员权限；普通成员可继续查看文档列表和授权来源，不能请求入库或查询任务错误详情。无库权限、不存在、已删除以及库/文档/任务链不匹配统一 404；普通成员调用管理员接口为 403。

- 上传：`POST /knowledge-bases/{kb_id}/documents`，multipart `file`。
- 显式入库：`POST /knowledge-bases/{kb_id}/documents/{document_id}/ingestions`，无需请求体，客户端不能指定用户或模型。
- 查询：`GET /knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job_id}`。

上传/入库响应示意（占位 ID，不是运行结果）：

```json
{
  "document_id": "document-uuid",
  "status": "uploaded",
  "job_id": "job-uuid",
  "job_status": "queued",
  "status_url": "/knowledge-bases/kb-uuid/documents/document-uuid/jobs/job-uuid",
  "request_id": "request-id"
}
```

HTTP 202 表示接受处理，不承诺入库成功；重复请求可能观察到任务已经终结。管理员用 status_url 查询 `status/attempts/build_id/error_code/error_summary/created_at/started_at/finished_at`。`uploaded` 只描述文件已保存，不代表索引 ready。

任务是管理员已提交的持久工作，worker 用本地受信任数据库身份执行；提交后撤销该用户权限不会自动取消已接受任务，但其下次状态查询会立即被拒绝。文档删除仍会由原入库服务阻止发布。

## PowerShell 启动与演示

在 `backend` 目录，先按 README 配置本地 PostgreSQL、`DATABASE_URL`、`JWT_SECRET`。API 和 worker 必须共享数据库及 `UPLOAD_STORAGE_DIR`（默认 `backend/var/uploads`）。应用启动不自动迁移：

```powershell
uv sync --locked
uv run --locked alembic upgrade head
```

使用新的 fake 演示知识库时，在 **API 终端、启动之前** 设置：

```powershell
$env:RETRIEVAL_EMBEDDING_BACKEND = "fake"
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

另开一个终端进入 `backend`，配置同一数据库、JWT_SECRET 和文件目录，只启动 **一个** worker：

```powershell
uv run --locked python -m app.worker --poll-seconds 1
```

任务使用 API 入队时保存的模型配置，fake/真实不会因 worker 环境后续变化而互换。真实模式 API 使用 `RETRIEVAL_EMBEDDING_BACKEND=openai`；worker 必须从环境读取 `OPENAI_API_KEY`。真实与 fake 使用隔离知识库，入库服务继续阻止同一有效索引混用配置。更改配置对已有 queued 任务无效。

按 README 登录并取得 `$session`、`$headers`、`$kb` 后，从仓库根目录上传：

```powershell
$base = "http://127.0.0.1:8000"
$sample = (Resolve-Path "data/sample_docs/a/A-EXP-001.md").Path
# 也可换成自己的演示 .txt/.md/.pdf 路径。
$uploaded = curl.exe -sS -H "Authorization: Bearer $($session.access_token)" -F "file=@$sample" "$base/knowledge-bases/$($kb.id)/documents" | ConvertFrom-Json
Invoke-RestMethod -Uri "$base$($uploaded.status_url)" -Headers $headers

# 仅在需要重新构建时显式请求；queued/running 状态会返回同一 job_id。
$job = Invoke-RestMethod -Method Post -Uri "$base/knowledge-bases/$($kb.id)/documents/$($uploaded.document_id)/ingestions" -Headers $headers
Invoke-RestMethod -Uri "$base$($job.status_url)" -Headers $headers
```

worker `--once` 最多领取一个任务：成功或空队列退出 0，正常任务失败退出 1，配置/数据库等导致 worker 停止退出 2，Ctrl+C 退出 130。常驻 worker 完成一个失败任务后可以继续处理下一个 queued 任务。不要把 `--once` 与常驻 worker 同时启动。

原 `python -m app.ingest_document ...` 保留供本地独立调试。它直接执行构建，不会同步队列任务状态；请停止 worker 后使用，且不要再以 CLI 完成视为任务已完成。

## 验收

设置 `TEST_POSTGRES_ADMIN_URL` 指向可创建数据库的本地测试服务器，再进入 `backend`：

```powershell
uv run --locked pytest -q tests/test_ingestion_jobs.py tests/test_documents.py tests/test_ingest.py tests/test_core_database.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
```

测试自行创建/删除随机数据库，不操作已有用户库。覆盖独立进程 worker、HTTP 上传不入库、queued/running 去重、并发首次排队、任务状态查询及撤权、失败重建保留有效索引、缺真实密钥不回退 fake、事务回滚清理、数据库外键/唯一约束及迁移升降级。模型调用中的断言验证无占用连接，另一个事务用 NOWAIT 获取任务/文档/构建锁以证明无遗留行锁。

## 本步限制

只支持一个 worker，API 与 worker 是两个独立进程。没有心跳、租约、自动重领、超时任务恢复、任务重试接口或分布式运行保证。即使领取使用数据库锁，也不能据此宣称整个任务系统具备多 worker 容错能力。

进程崩溃、强制终止或终态写库失败可能留下 running；构建发布和任务终态分开提交，可能出现文档 ready 而任务 running。排队任务仍持久保存，但 running 不会自动恢复，且会继续阻止同文档新任务。文件落盘与数据库之间的崩溃清理也未在本步解决。以上留待第 20B 步；本步不提供破坏性的手工清理命令。

官方依据：[SQLAlchemy 事务边界](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)、[PostgreSQL 部分唯一索引](https://www.postgresql.org/docs/current/indexes-partial.html)、[SELECT 行锁](https://www.postgresql.org/docs/current/sql-select.html)、[FastAPI 响应状态](https://fastapi.tiangolo.com/tutorial/response-status-code/)。未新增依赖，沿用已锁定版本。

## 实际验证记录

本步定向组 `19 passed`；完整回归 `180 passed, 2 skipped`，均有 1 个既有 TestClient 弃用警告，跳过项为真实模型检查。Ruff、格式、依赖锁和 Alembic 模型差异检查通过。独立子进程 `app.worker --once` 实际完成 fake 任务，第二次返回 idle；未调用真实模型。

一份 20 字节 TXT 经 TestClient 上传的单次观测为 39.819 ms（n=1），请求内禁止执行入库仍成功返回 queued。此检查只验证异步边界与本机小文件正常路径，不是生产性能目标。迁移回退和所有数据库写入均在随机临时测试库进行，结束后删除；专用测试容器已停止。
