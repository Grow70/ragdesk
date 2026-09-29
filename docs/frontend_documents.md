# 文档管理页面（第 26B 步）

## 使用范围

登录后选择知识库，点击“打开文档”。每页 10 份，支持切换知识库、分页和手动刷新。

- 管理员：上传、查看最近任务及失败代码/摘要、预览、重建和确认删除。
- 普通成员：列表与已发布切块预览；任务详情继续按原契约仅管理员可见。
- 上传前展示 `.md` / `.txt` / 文本型 `.pdf`、UTF-8、最多 10 MiB（10 × 1024 × 1024 字节）；浏览器检查只是提前反馈，后端仍检查权限、实际格式和大小。
- 预览最多 3 块 × 600 字符，保留实际页码/标题/行号；标题最多 6 层 × 160 字符，有截断标记。仅查看当前发布构建，不在读取时解析文件。HTML/Markdown 作为纯文本展示。
- 文档“可检索”与“最近重建任务失败”可以同时存在，表示旧索引仍生效。重建不覆盖原文件；删除后新预览请求返回 404。

## 启动（PowerShell）

先按 README 配置数据库、迁移、JWT_SECRET 与演示账号。以下命令均从仓库根目录开始；API 和 worker 必须使用相同数据库和 UPLOAD_STORAGE_DIR，每个终端都要配置自己的环境变量。

终端一，启动 API：

```powershell
cd backend
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

终端二，启动**唯一一个** worker：

```powershell
cd backend
uv run --locked python -m app.worker
```

终端三，启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`。真实模型使用后端已有环境变量配置，前端不需要模型密钥。离线演示请在 API/worker 启动前显式设 `$env:RETRIEVAL_EMBEDDING_BACKEND = "fake"`，并使用独立 fake 知识库，不能混入真实有效索引。没有 worker 时任务会保持排队，页面不会伪造完成。

## 轮询和取消

每个可见的活动任务在上一请求完成 2 秒后继续查询；单轮最多 60 次且不超过 120 秒。终态、请求错误或到期均停止；点击“重新检查任务”可开启新一轮。离开文档页、切库、分页/刷新替换列表、退出或 pagehide 时清理定时器并 abort 在途请求；客户端再次检查取消信号，避免迟到数据覆盖新页面。

单请求仍有 15 秒上限。停止浏览器查询**不等于取消后台任务**；只有已有删除接口会撤销该文档活动任务。初版每个页面最多 10 个任务轮询，未使用推送或全局无限定时器。后台标签页计时器可能被浏览器节流，因此 2 秒是请求间最小计划间隔，不是实时状态承诺。

## 后端契约

所有路径经前端 `/api` 代理，以下为 FastAPI 实际路径。

| 操作 | 方法与路径 | 权限 |
| --- | --- | --- |
| 分页列表 | `GET /knowledge-bases/{kb_id}/documents?limit=10&offset=0` | 成员 |
| 文档详情 | `GET /knowledge-bases/{kb_id}/documents/{document_id}` | 成员 |
| 上传 | `POST /knowledge-bases/{kb_id}/documents`，multipart `file` | 管理员 |
| 重建 | `POST /knowledge-bases/{kb_id}/documents/{document_id}/rebuild` | 管理员 |
| 删除 | `DELETE /knowledge-bases/{kb_id}/documents/{document_id}` | 管理员 |
| 任务 | `GET /knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job_id}` | 管理员 |
| 切块预览（新增） | `GET /knowledge-bases/{kb_id}/documents/{document_id}/preview` | 成员 |

文档列表/详情的 `latest_job` 对管理员返回现有 JobResponse，对普通成员为 null。该字段使页面刷新或重新登录后仍能发现已有任务，不依赖浏览器保存上传响应。当前小规模实现沿用逐文档查询，未宣称批量 SQL 或大规模列表性能。

预览响应示意（ID 为示意占位符）：

```json
{
  "document_id": "<uuid>",
  "build_id": "<uuid>",
  "items": [{
    "chunk_id": "<uuid>", "ordinal": 0,
    "text": "演示数据：报销上限 680 元。", "truncated": false,
    "page_number": null, "heading_path": ["报销规则"],
    "start_line": 5, "end_line": 5, "locator_truncated": false
  }],
  "total_chunks": 1,
  "request_id": "<request_id>"
}
```

尚无发布构建时 `build_id=null, items=[], total_chunks=0`；这与技术故障不同。SQL 在同一查询快照中限定当前 active、ready、未删除文档，返回有界文本，不加载或返回向量。不存在、外库、撤权或删除对象统一 404；缺失/过期身份为 401。没有前端角色能替代后端授权。

## 可复制验收命令

浏览器模拟 HTTP、可控时钟测试（不需要模型/数据库）：

```powershell
cd frontend
npm ci
npx playwright install chromium
npm run build
npm run format:check
npm test
```

真实 PostgreSQL + API + 单 worker + 浏览器，Embedding 明确为 fake：先在 frontend 安装依赖和 Chromium，然后从仓库根目录执行。`TEST_POSTGRES_ADMIN_URL` 应指向可创建/删除临时库的**专用测试实例**，不要指向已有用户数据。

```powershell
cd backend
$env:TEST_POSTGRES_ADMIN_URL = "postgresql+psycopg://测试账号:测试密码@127.0.0.1:测试端口/postgres"
$env:RUN_FRONTEND_E2E = "1"
uv run --locked pytest -q tests/test_document_preview.py tests/test_documents.py tests/test_document_lifecycle.py tests/test_ingestion_jobs.py tests/test_frontend_documents.py
```

真实测试夹具会创建随机数据库、演示账号和临时上传目录，启动/清理 API 与 worker；测试结束删除随机数据库。需要本机 5173 端口空闲。没有调用真实模型，不将 fake 入库当语义质量验证。手工可按管理员上传→完成→预览→重建→删除，再以成员验证只读操作。

实际执行结果与问题修复记录见 [进度](progress.md)。浏览器测试不保存 trace/视频或令牌，截图仅含模拟资料，产物在 Git 忽略目录 `artifacts/validation/step26b/`。目前仅自动验证 Chromium，PowerShell 命令未在 Windows 原生环境执行。

## 原理与面试准备

**知识点：异步生命周期与资源清理。** 请求完成后安排下一次 setTimeout 可避免慢请求重叠；useEffect cleanup 清理计时器并取消请求，响应返回前再次检查取消信号，阻止旧页面的请求改变新页面。

1. **为什么隐藏上传按钮不算权限控制？** 客户端可被绕过；所有写接口仍 require_kb_admin，预览/list 仍 require_kb_member，SQL 同时限制知识库和当前有效构建。真实测试直接调用成员的上传/重建/删除，验证 403。
2. **重建失败为什么仍显示可检索？** 任务状态与生效索引是不同状态。新构建完整成功后才原子切换 active_build_id；失败保留旧构建，界面分别显示文档状态和任务错误。

官方参考：[React useEffect 的 cleanup](https://react.dev/reference/react/useEffect)、[AbortController](https://developer.mozilla.org/en-US/docs/Web/API/AbortController)、[FormData 与 multipart boundary](https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest_API/Using_FormData_Objects)。本步无新增依赖，沿用前后端锁文件。
