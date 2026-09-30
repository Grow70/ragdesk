# Ragdesk

面向模拟企业资料的知识库问答系统。当前提供认证、知识库权限、文档与解析、固定 RAG 问答、检索评测，以及数据库入库任务和单 worker。真实模型效果的验证状态见各步骤实验记录。

需求和后续实现契约分别见 [docs/requirements.md](docs/requirements.md) 与 [docs/architecture.md](docs/architecture.md)。

## CI 与关键回归

[第 29 步 CI 说明](docs/ci.md)包含工作流、本地复现命令和逐项覆盖映射。GitHub Actions 运行真实 PostgreSQL+pgvector 集成、前端构建及 fake 模型浏览器闭环；缺数据库或意外跳过会失败。自动 CI 不注入收费 API 密钥，真实模型烟测独立手动运行。实际已执行/未执行状态见[进度记录](docs/progress.md)。

## 本地 Docker Compose：从空环境到首次问答（PowerShell）

前提：安装并启动 Docker Desktop（Linux containers，Compose v2+），下载本仓库。无需在宿主机安装 Python/Node。以下从仓库根目录执行。默认仅 `http://127.0.0.1:8080` 可访问；数据库和后端不发布宿主机端口，不用于公网部署。

### 1. 创建本地配置并构建

仅首次创建 `.env`，不要覆盖已有配置。示例没有默认密码；下面生成本机使用的随机数据库密码和 JWT 密钥，并保存到 Git 忽略的文件。不要提交或分享该文件。Compose 注入环境变量，Python 本身不读取 `.env`。

```powershell
if (Test-Path .env) { throw '.env 已存在，请保留并检查配置' }
Copy-Item .env.example .env
function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$config = Get-Content .env -Raw
$config = $config.Replace('POSTGRES_PASSWORD=', ('POSTGRES_PASSWORD=' + (New-LocalSecret)))
$config = $config.Replace('JWT_SECRET=', ('JWT_SECRET=' + (New-LocalSecret)))
[IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $config, (New-Object Text.UTF8Encoding($false)))
Remove-Variable config

docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose 配置不完整' }
docker compose build
if ($LASTEXITCODE -ne 0) { throw '镜像构建失败，请先排查构建日志' }
```

默认 `COMPOSE_PROJECT_NAME=ragdesk-fake`、`RAGDESK_MODE=fake`。后端 `uv.lock` 与前端 `package-lock.json` 分别通过 `uv sync --locked --no-dev` 和 `npm ci` 安装；Python 3.12.13、uv 0.12.15、Node 24.21.0、Nginx 1.28.3、pgvector 0.8.6/PG17 镜像同时固定标签和 SHA-256 摘要。`.dockerignore` 使用白名单，不把密钥、上传资料或宿主机依赖目录送入构建上下文。

### 2. 显式迁移、初始化演示账号、启动四个服务

```powershell
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw '数据库未就绪' }
docker compose --profile init run --rm migrate
if ($LASTEXITCODE -ne 0) { throw '迁移失败，暂不启动应用' }
docker compose run --rm --no-deps backend init-demo --login-name alice --display-name Alice
if ($LASTEXITCODE -ne 0) { throw '演示用户创建失败' }
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { throw '服务未就绪，请检查 compose ps/logs' }
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/api/health/ready
```

初始化账号时交互输入并确认至少 12 字符的自选密码；无默认账号、自动注册或生产默认凭据。同名账号再次初始化会明确失败，不重置密码。后续启动无需重复创建账号。

只有显式 `migrate` 服务执行 Alembic；API/worker 不修改表结构。迁移入口用 PostgreSQL 会话 advisory lock 阻止并发迁移。`/api/health/live` 检查进程，`/api/health/ready` 检查数据库连接、vector 扩展和 Alembic head，未迁移时返回 503；就绪不代表模型服务可用。

### 3. 登录、创建库、上传并等待 worker，再提问

也可直接打开 `http://127.0.0.1:8080` 登录、选择库并操作页面；首次空库列表需先用以下已有接口创建知识库。前端令牌仅在内存，刷新需重新登录。

```powershell
$api = 'http://127.0.0.1:8080/api'
$credential = Get-Credential -UserName alice -Message '输入刚才创建的演示密码'
$body = @{ login_name = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json
$session = Invoke-RestMethod -Method Post -Uri "$api/auth/session" -ContentType application/json -Body $body
Remove-Variable body, credential
$headers = @{ Authorization = "Bearer $($session.access_token)" }
Invoke-RestMethod "$api/auth/me" -Headers $headers
$kbBody = @{ name = 'Demo Fake' } | ConvertTo-Json
$kb = Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases" -Headers $headers -ContentType application/json -Body $kbBody
$sample = (Resolve-Path data/sample_docs/a/A-EXP-001.md).Path
$uploadJson = curl.exe --fail-with-body -sS -H "Authorization: Bearer $($session.access_token)" -F "file=@$sample" "$api/knowledge-bases/$($kb.id)/documents"
if ($LASTEXITCODE -ne 0) { throw '上传失败' }
$uploaded = $uploadJson | ConvertFrom-Json
$job = $null
for ($i = 0; $i -lt 60; $i++) {
    $job = Invoke-RestMethod "$api$($uploaded.status_url)" -Headers $headers
    if ($job.status -notin @('queued', 'running')) { break }
    Start-Sleep -Seconds 2
}
$job
if ($job.status -ne 'succeeded') { throw '任务失败或等待超时，检查任务错误与 worker 日志' }
$question = @{ question = '报销期限是多少？'; mode = 'rag' } | ConvertTo-Json
$answer = Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases/$($kb.id)/answers" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($question))
$answer | ConvertTo-Json -Depth 12
# 如需检查 Agent 界面闭环，将上面的 mode 改为 agent 后再提问。
```

**fake 演示不代表模型效果**：fake 向量不具备语义能力；回答显著标注 FAKE，只回显一个授权片段，不判断其能否回答问题；fake Agent 固定一次搜索后结束。用途是验证上传、任务、检索、引用和界面连通性，不能用于准确率或 Agent 决策能力结论。

### 4. 持久化、重启和数据库恢复

`pgdata` 保存数据库，`uploads` 由 backend/worker 共享并保存原文件；卷按 Compose 项目名隔离。运行一个 worker，**不要使用 `--scale worker=N`**。文件由后端非 root 用户写入，不挂载到前端静态目录。

```powershell
# 保留卷，重建容器；不加 -v。
docker compose down
docker compose up -d --wait
# 用原账号登录：原知识库、文档和来源应仍在。
docker compose logs --tail 60 worker

# 本地故障演练：停库期间请求可能失败；恢复后连接应重新取得。
docker compose stop db
docker compose up -d --wait db
docker compose up -d --wait
Invoke-RestMethod http://127.0.0.1:8080/api/health/ready
```

SQLAlchemy `pool_pre_ping` 检查从连接池取出的连接；不重放失败中的写事务。worker 数据库故障退出后由 `restart: unless-stopped` 重启，持久任务按已有租约、run_token 与最多 3 次尝试恢复；外部模型调用可能重复，不承诺端到端 exactly-once。手动 `stop worker` 后须显式 `start worker` 或 `up -d`。

`down` 保留资料；`down -v` 会删除该项目的卷，仅用于确认可丢弃的验收环境。不要对已有资料执行。保留原 `.env`：修改数据库密码不会自动更新已有数据库角色；更换 JWT 密钥会使原令牌失效。

### 5. 切换真实模型（另建项目与卷）

将 `.env` 复制为 `.env.real`（同样被 Git 忽略），修改：

```dotenv
COMPOSE_PROJECT_NAME=ragdesk-real
RAGDESK_MODE=real
FRONTEND_PORT=8081
OPENAI_API_KEY=<在本地填写自己的密钥，不提交>
CHAT_MODEL=gpt-4.1-mini-2025-04-14
EMBEDDING_MODEL=text-embedding-3-small
```

为 real 配置独立随机数据库密码/JWT 密钥；所有命令改为 `docker compose --env-file .env.real ...`，重复构建、显式迁移、初始化账号、启动，并使用 8081。重新上传到新的真实索引；不要直接改已有 fake 项目模式或复用其卷。向量维度固定 1536，与现有迁移兼容，变更模型/维度须走契约和迁移，不能直接混用。real 缺密钥明确失败，模型故障不会降级成 fake。真实调用按提供商计费，本步未执行真实模型验证；详见[模型配置](docs/model_config.md)。

### 验收与排错

宿主 Python 测试环境准备好后，可运行离线入口检查：

```powershell
Set-Location backend
uv sync --locked
uv run --locked pytest -q tests/test_container_runtime.py
uv run --locked ruff check app/container_runtime.py tests/test_container_runtime.py
Set-Location ..
```

独立 Compose 验收（需要宿主 Python/uv；会构建镜像并创建随机测试项目，结束只删除它自己的测试卷，不读取你的 `.env` 或使用真实模型密钥）：

```powershell
# 仓库根目录；使用前面 uv sync --locked 创建的测试环境。
.\backend\.venv\Scripts\python.exe backend/tests/compose_acceptance.py
```

报告位于 `artifacts/validation/step28/ragdesk-acceptance-<随机值>/report.json`，原始命令输出与 fake 回答在同目录；全部被 Git 忽略。WSL 可用 `backend/.venv/bin/python backend/tests/compose_acceptance.py --docker /mnt/d/soft/Docker/resources/bin/docker.exe`（按本机 Docker CLI 路径调整）。本步实际验证 WSL + Docker Desktop 的 Linux/amd64 容器，10 个端到端检查通过；Windows 原生 PowerShell、ARM64 与真实模型调用未验证。这里的 fake 检查不是模型效果测试。

镜像下载与依赖安装需要网络。Docker Hub 的认证/连接错误应检查 Docker Desktop 与当前终端的代理配置；不要通过删卷或更改应用密码解决网络下载失败。日志可用 `docker compose logs --tail 80 backend worker` 查看；避免分享 `.env` 或展开后的 `docker compose config`（含密钥），用 `config --quiet` 检查即可。实际验收及未验证项见 [进度记录](docs/progress.md)。

官方依据：[Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/)、[Docker 卷](https://docs.docker.com/engine/storage/volumes/)、[uv 容器集成](https://docs.astral.sh/uv/guides/integration/docker/)、[SQLAlchemy 断线处理](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-pessimistic)。

> 以下各步保留的 `http://127.0.0.1:8000` 示例是宿主机开发入口。使用本 Compose 时将其替换为 `http://127.0.0.1:8080/api`；数据库默认不发布 5432，宿主机 Python 开发需自行提供独立本地开发库及环境变量，不与 Compose 的容器地址 `db` 混用。

## 知识库与成员权限

已登录用户可创建知识库，并自动成为该库管理员。`GET /knowledge-bases` 只返回当前用户加入的库；`GET /knowledge-bases/{kb_id}` 要求成员资格。管理员可通过 `GET /knowledge-bases/{kb_id}/members` 查看成员，使用 `PUT /knowledge-bases/{kb_id}/members/{user_id}` 配合 `{"role":"member"}` 或 `{"role":"admin"}` 添加或调整已有用户，使用 `DELETE` 同路径移除成员。移除或降级最后一位管理员会返回 `409`。普通成员可读取知识库及其文档，上传仅限管理员；文档删除、问答及来源查看见下文。

在上面的登录示例取得 `$session` 后，可创建并查看知识库：

```powershell
$headers = @{ Authorization = "Bearer $($session.access_token)" }
$kb = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/knowledge-bases -Headers $headers -ContentType application/json -Body '{"name":"A"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/knowledge-bases -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)" -Headers $headers
```

集成测试以三个临时用户和两个独立知识库验证成员隔离。服务端只从已验证的 JWT 取得操作人身份，成员变更提交后，下次请求重新查询数据库权限。

## 原文件上传与读取

管理员可通过 `POST /knowledge-bases/{kb_id}/documents` 的 `file` 表单字段上传 `.md`、`.txt` 或 `.pdf`，单文件最多 10 MiB。服务端检查文本编码或 PDF 的基本头尾结构并计算 SHA-256；同一库中相同有效字节返回已有 `document_id`。第 20A 步起返回 `202`、`document_id`、`status: uploaded`、`job_id`、`job_status` 与 `status_url`。新文件与任务同事务提交；重复文件复用已有文档及活动/最近任务。`uploaded` 表示原文件已保存，后台 worker 完成构建发布后才可检索，管理员可轮询 `status_url`；PDF 的基础检查不能证明含有可提取文本，扫描页会在解析时报告不完整。

成员可通过 `GET /knowledge-bases/{kb_id}/documents?limit=20&offset=0` 分页查看文档，通过 `GET /knowledge-bases/{kb_id}/documents/{document_id}` 查看详情，并从 `GET /knowledge-bases/{kb_id}/documents/{document_id}/raw` 下载原文件。所有读取都重新检查当前成员资格。私有文件默认写到 `backend/var/uploads`，可设置 `UPLOAD_STORAGE_DIR` 指向其他**不公开映射**的目录；原始文件和数据库文件不要提交到 Git。

在仓库根目录、已按上文取得 `$headers` 与 `$kb` 后，可上传一份模拟资料并读取：

```powershell
$sample = (Resolve-Path data/sample_docs/a/A-EXP-001.md).Path
$uploaded = curl.exe -sS -H "Authorization: Bearer $($session.access_token)" -F "file=@$sample" "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/documents" | ConvertFrom-Json
$uploaded
Invoke-RestMethod -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/documents?limit=20&offset=0" -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/documents/$($uploaded.document_id)" -Headers $headers
Invoke-WebRequest -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/documents/$($uploaded.document_id)/raw" -Headers $headers -OutFile "$env:TEMP\ragdesk-download.md"
```

## Markdown/TXT 解析预览

第 9 步的解析器接收受控本地 `.md` 或 `.txt` 路径，输出按原文顺序排列的 `ParsedSection` JSON。`source_locator` 与 `start_line`、`end_line` 指向原文行号；Markdown 保留标题路径、列表标记和代码围栏，TXT 的标题路径为空。支持 UTF-8 和带 UTF-8 BOM 的文件；错误编码、空正文、缺文件、未闭合代码围栏等会在标准错误输出稳定错误码。预览不写数据库、不执行代码或访问链接，也不切块。

在仓库根目录运行：

```powershell
Set-Location backend
uv run --locked python -m app.parsers.cli ..\data\sample_docs\a\A-EXP-001.md
```

已有数据库文档 ID 时可追加 `--document-id <ID>`；独立预览时输出的 `document_id` 为 `null`。此命令不需要数据库或模型配置。

## 文本型 PDF 解析预览

第 10 步使用 pypdf 逐页提取 PDF 文字，复用 `ParsedSection`。`page_number` 和 `source_locator` 记录从 1 开始的**物理页码**；无文字的空白页或图片页不会生成正文 section，但会在 `PdfParseResult.warnings` 中逐页报告，状态为 `partial`。整份无可提取文字、加密或损坏时返回明确的解析错误。暂不支持 OCR、复杂表格结构恢复和复杂双栏排版；PDF 中隐藏的 OCR 文字层也不能保证正确。

在 `backend` 目录运行以下 PowerShell 命令查看固定测试 PDF 的页码、状态和警告：

```powershell
uv run --locked python -c "from app.parsers.pdf import parse_pdf; r = parse_pdf('tests/fixtures/pdf_mixed.pdf'); print(r.status); print([(s.page_number, s.text) for s in r.sections]); print([(w.page_number, w.code) for w in r.warnings])"
uv run --locked pytest -q tests/test_pdf_parser.py
```

## 切块预览

第 11 步的 `chunk_sections` 接收解析器输出的 `ParsedSection` 列表，返回 `ChunkDraft` 与 `ChunkStats`，不写数据库或调用模型。默认每块最多 600 个 Python 字符（Unicode 码点），拆分时重叠 80 个字符；可通过 `ChunkConfig(chunk_size=..., overlap=...)` 调整。优先保留标题、段落、句子边界，超长内容才按字符切；同一 PDF 的不同物理页不会拼接。草稿的 `source_spans` 记录 section 内字符范围和原始页码或行号，后续构建时才能分配数据库 ID。

在 `backend` 目录运行以下 PowerShell 命令预览模拟资料并验收：

```powershell
uv run --locked python -c "from app.parsers.text import parse_file; from app.chunking import chunk_sections; r = chunk_sections(parse_file('../data/sample_docs/a/A-EXP-001.md')); print(r.stats); print([(c.ordinal, c.heading_path, c.page_number, c.text) for c in r.chunks])"
uv run --locked pytest -q tests/test_chunker.py
```

重叠能让跨切分点的事实在相邻块中保有上下文，提高这类问题的召回机会；也会增加存储、嵌入成本和相近检索结果。统计中的 `duplicate_chunks` 只表示正文完全相同，`short_chunks` 指短于块上限一半，不能据此推断真实检索效果。

## 模型适配层（尚未接入 RAG）

第 12 步提供 OpenAI 聊天和向量客户端，以及需由测试显式创建的离线 fake。真实客户端缺少 `OPENAI_API_KEY` 会报错，真实请求失败不会回退 fake。默认模型分别是 `gpt-4.1-mini-2025-04-14` 和 `text-embedding-3-small`，向量配置为 1536 维。模型约束和来源见[模型配置](docs/model_config.md)。当前**真实接口未验证**。

在 `backend` 目录运行离线验收，无需密钥或数据库：

```powershell
uv run --locked pytest -q tests/test_llm.py
uv run --locked ruff check app/llm app/config.py tests/test_llm.py
```

若将来提供自己的 API 密钥，可在独立会话显式运行一次真实冒烟检查；它会产生一条嵌入和一条聊天请求，按用量计费，不用于衡量 RAG 效果：

```powershell
$env:OPENAI_API_KEY = "<your-key-in-process-environment>"
uv run --locked python -c "import os; from app.llm.openai import OpenAIEmbeddingClient, OpenAIChatClient; e = OpenAIEmbeddingClient(api_key=os.environ['OPENAI_API_KEY']); c = OpenAIChatClient(api_key=os.environ['OPENAI_API_KEY']); print('embedding:', len(e.embed_query('演示资料').vectors[0])); print('chat:', c.generate([{'role':'user','content':'请回答：收到'}], {'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}).content); e.close(); c.close()"
```

## 单文档命令行入库

第 13 步仅提供本地操作命令，处理**已上传**且未删除的文档；命令使用当前 `DATABASE_URL` 的数据库权限，须在可信的本地开发环境运行。第 20A 步新增后台任务和 HTTP 入库请求后，本 CLI 仍保留供独立调试；不要与 worker 并行使用，也不要把 CLI 完成等同于已有任务状态已同步。先执行 `uv run --locked alembic upgrade head`，显式加入 `vector(1536)` 列及构建配置字段。命令从私有存储读取文件，解析、切块、分批嵌入并写候选构建；所有块齐全且与同库有效索引配置兼容后才发布。PDF 有无文字页警告时标为失败；失败构建不可检索，旧有效构建保持有效。

在 `backend` 目录、已设置 `DATABASE_URL`、`JWT_SECRET` 且按上文取得 `$uploaded` 后，使用 PowerShell：

```powershell
uv run --locked alembic upgrade head
$buildId = [guid]::NewGuid().ToString()
uv run --locked python -m app.ingest_document --document-id $uploaded.document_id --build-id $buildId --embedding-backend fake
```

输出 JSON 包含 `build_id`、`status`、`chunk_count`、`model_config_id`、`reused` 和失败码。用同一个 `--build-id` 再执行会返回已有构建状态，不重复调用模型或写块；失败后重建需使用新的 ID。`--chunk-size`、`--overlap` 和 `--batch-size` 可调整。真实请求须**显式**改用 `--embedding-backend openai` 并在环境中提供 `OPENAI_API_KEY`；不自动切换 fake。fake 与 OpenAI 的配置 ID 不同，同一个知识库不允许同时发布两种有效配置。fake 结果只验证入库流程，不代表检索或 RAG 效果。

数据库集成测试会自行创建并删除随机命名的测试库；按下文配置 `TEST_POSTGRES_ADMIN_URL` 后，可在 `backend` 目录运行：

```powershell
uv run --locked pytest -q tests/test_ingest.py
```

## 向量检索调试

第 14 步提供 `POST /knowledge-bases/{kb_id}/search`，只返回片段，不生成回答。请求含非空 `query` 和可选 `top_k`（默认 5，范围 1～20）。响应的 `distance_metric` 为 `cosine_distance`；`distance` 越小，向量越相似，`rank` 从 1 开始。距离不表示答案正确概率。检索仅查看当前库未删除文档的 ready 有效构建，并要求查询与文档的嵌入模型配置标识一致。空库返回空 `items`。未授权库与不存在的库均返回 `404`；模型故障返回错误响应。

服务默认使用 OpenAI 查询向量，需要进程环境中的 `OPENAI_API_KEY`。若前一步显式以 `--embedding-backend fake` 入库，在**启动 API 服务前**设 `$env:RETRIEVAL_EMBEDDING_BACKEND = "fake"`，只查询 fake 配置的索引；这是离线流程检查，fake 向量不具备语义效果。请勿将 fake 的距离当作真实语义检索实验。

在 PowerShell 中，取得上文的 `$session`、`$kb`，且对应文档已完成入库后运行：

```powershell
$headers = @{ Authorization = "Bearer $($session.access_token)" }
$body = @{ query = "报销期限是多少？"; top_k = 5 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/search" -Headers $headers -ContentType application/json -Body $body
```

离线数据库检查使用随机命名并在完成后删除的测试库：

```powershell
uv run --locked pytest -q tests/test_retrieval.py -k "not real_semantic"
```

真实语义检查是**独立的显式操作**：在 `backend` 目录设置 `TEST_POSTGRES_ADMIN_URL`、`OPENAI_API_KEY` 及 `$env:RUN_REAL_RETRIEVAL = "1"`，运行 `uv run --locked pytest -q -s tests/test_retrieval.py -k real_semantic`。它将两段短文本和一个问题发送到 OpenAI Embeddings API，预计两次请求（此检查限制每次最多一次尝试），会产生少量费用。测试打印两条距离并检查报销文档排在运维文档前；结果需单独记录，不能用 fake 测试结果代替。

## 固定流程 RAG 问答

第 15 步提供 `POST /knowledge-bases/{kb_id}/answers`。请求为 `{"question":"报销期限是多少？","top_k":5}`；问题最多 4000 字符，`top_k` 默认 5、允许 1～20。流程复用成员授权和向量检索，为实际装入上下文的片段分配本次请求的 `c1`、`c2` 等编号，调用 Chat 后校验结构和引用。响应包含 `status`、`answer`、`citations`、`request_id`；引用的文档名、页码、标题、行号和原文均由后端读取，模型只提交引用编号。

无可用证据时直接返回 `insufficient_evidence`，不调用 Chat。有片段但不足以回答时允许 `insufficient_evidence` 或 `needs_clarification`，引用为空。冲突应在回答中呈现各方说法和各方引用。模型返回假引用、成功回答缺少引用、非法 JSON/schema 时响应 `502`；模型超时为 `504`。生成期间资料被删除、内容改变或构建切换则响应 `409 EVIDENCE_CHANGED`；权限撤销为 `404`。

真实问答使用进程环境中的 `OPENAI_API_KEY`、`CHAT_MODEL` 和现有 Embedding 配置。使用以 `--embedding-backend openai` 建成的知识库，并在启动 API 前设置 `$env:RETRIEVAL_EMBEDDING_BACKEND = "openai"`；查询和文档的模型配置必须一致。已有 fake 索引不因设置变化自动变成真实索引。没有密钥时不会自动用 fake Chat 生成回答；离线验收通过测试显式注入 fake。

在已启动 API、已登录取得 `$session` 并选定 `$kb` 后，PowerShell 调用示例：

```powershell
$headers = @{ Authorization = "Bearer $($session.access_token)" }
$body = @{ question = "报销期限是多少？"; top_k = 5 } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)/answers" -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$result | ConvertTo-Json -Depth 8
if ($result.citations.Count -gt 0) {
    Invoke-RestMethod -Uri "http://127.0.0.1:8000$($result.citations[0].source_path)" -Headers $headers
}
```

来源接口为 `GET /knowledge-bases/{kb_id}/sources/{document_id}/{build_id}/{chunk_id}`。每次访问都重新校验当前成员资格、完整 ID 链、文档未删除及当前 ready 构建；旧构建、删除资料、跨库或错误 ID 均不可读取。

上下文预算默认 16384 个保守计量单位：系统提示、问题、完整消息 JSON 和 schema 按 UTF-8 字节计算，另预留 1024 token 输出与 1024 消息封装余量。剩余空间只装完整证据块，放不下时省略并告诉模型范围不完整，不截断片段尾部的例外条款。这不是精确 token 计数；`ContextBudget` 可由服务调用方显式配置，切换模型需要重新核对窗口。实际 Chat 请求带 `max_completion_tokens` 输出上限。

在 `backend` 目录、按下文设置测试服务器后运行离线验收；测试创建并删除独立数据库，不调用真实模型：

```powershell
uv run --locked pytest -q tests/test_answers.py
```

**引用 ID 合法只验证来源，不能自动证明答案受到证据支持。** 系统提示要求只依据资料，并把资料内的恶意指令当作内容。fake 测试验证消息隔离、冲突响应透传、引用校验和失败分支；真实模型的事实有据率、冲突识别和抗提示注入效果仍需独立评测，本步没有这些效果结论。

## 数据库结构

应用启动不会创建或修改表。在 `backend` 目录中，确认 `DATABASE_URL` 指向预期的**新建开发数据库**后，显式执行迁移：

```powershell
uv run --locked alembic upgrade head
uv run --locked alembic current
```

首个迁移创建六张核心表，第二个迁移给用户添加可空登录名和 Argon2id 哈希列，以保留旧用户；均不包含向量列。迁移回退只在临时测试库中验证；不要对已有资料的数据库运行 `alembic downgrade base`，回退凭据迁移会删除登录名和密码哈希。

若要运行数据库集成检查，在 `backend` 目录设置测试服务器连接（指向 Compose 的默认 `postgres` 库），测试会自行创建并删除名称随机的独立数据库，不改动 `ragdesk` 库：

```powershell
$env:TEST_POSTGRES_ADMIN_URL = "postgresql+psycopg://ragdesk:$($env:POSTGRES_PASSWORD)@127.0.0.1:5432/postgres"
uv run --locked pytest -q
```

第一次创建数据库卷时，Compose 初始化脚本会启用 `vector` 扩展。已有卷不会重跑初始化脚本；必要时在仓库根目录执行 `docker compose exec db psql -U ragdesk -d ragdesk -c "CREATE EXTENSION IF NOT EXISTS vector;"`。Compose 镜像已固定标签与摘要，并只在本机回环地址暴露数据库端口。

## 检查

在 `backend` 目录运行：

```powershell
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

缺少必需配置时，应用启动会指出缺少或无效的变量名，不输出变量值。验证结束后，在仓库根目录运行 `docker compose down` 可停止开发数据库，数据卷会保留。

## 向量检索基线评测

第 16 步评测脚本默认选择 dev 并只做预检，在 `backend` 目录执行：

```powershell
uv run --locked python -m app.evaluate
uv run --locked pytest -q tests/test_evaluation.py tests/test_evaluation_runtime.py
```

每次保存 `results.jsonl`、`summary.json`、`report.md`、`manifest.json` 和题目原文到独立 `artifacts/eval/` 目录。当前 30 条样本全部为 draft，预检会退出 2 并记录 `UNREVIEWED_SAMPLES`，效果值为 null；这不构成真实向量检索或 RAG 效果基线。

完成人工复核、真实模型入库、库映射和环境配置后，使用 `--run-real --mapping <映射文件>` 显式运行真实评测。具体命令、分母、严格原文/位置匹配、模型费用上界和人工审核要求见 [评测说明](docs/evaluation.md)。test 需显式 `--split test` 并通过冻结摘要校验，不能用于反复调参。数据库测试使用 fake，不发真实模型请求；没有测试库配置时集成项跳过。

## BM25 单路检索

第 17 步新增 `app.services.bm25.search` 和独立评测 CLI，使用锁定的 jieba 与 rank-bm25。每请求重新读取授权库的 ready active 块并建立集合；保留错误码、英文缩写及完整产品号，零/负 BM25 分数不会导致词面匹配被误删。统一结果中的 `bm25_score` 越大排名越前，`distance=null`。本步没有融合，也未替换现有向量问答。

在 `backend` 目录运行：

```powershell
uv sync --locked
uv run --locked python -m app.evaluate_bm25
uv run --locked pytest -q tests/test_bm25.py tests/test_bm25_evaluation.py
```

配置数据库、`EVAL_BEARER_TOKEN` 和 A/B 库映射后，可加 `--run --draft-diagnostics --mapping <映射文件>` 保存 dev 草稿的实际原始候选与成本；正式效果指标仍为 null。完成人工复核后去掉 `--draft-diagnostics`，才生成正式检索指标。无需真实模型密钥。产物写入独立目录，保留原向量报告。预处理、分数含义、成本测量与完整 PowerShell 命令见 [BM25 说明](docs/bm25.md)。

## RRF 融合与三路对比

第18步提供 `app.services.hybrid.search`：向量与BM25各取最多20，按chunk_id去重，使用 `Σ 1/(60+rank)` 返回默认前5，保留各路原始排名。只融合排名，不直接相加原始分数。降级默认关闭；显式配置后仅允许白名单临时故障，权限和数据变化错误始终失败。现有问答和 `/search` 继续使用原向量服务。

在 `backend` 目录执行：

```powershell
uv run --locked python -m app.evaluate_rrf
uv run --locked pytest -q tests/test_rrf.py tests/test_hybrid.py tests/test_rrf_evaluation.py
```

真实比较需已复核 dev、兼容真实索引、登录令牌和API配置，再显式传入 `--run-real --mapping <映射文件>`。离线 `--fake-diagnostics` 只验证三路流程，不能说明RRF是否提升了语义检索。每次保存独立报告，复用同次候选作公平对照，原向量和BM25产物保留。配置、手算例子、降级规则及完整命令见 [RRF 说明](docs/rrf.md)。

## 可关闭的重排器

第 19 步新增 `app.services.reranked.search_configured`：按 `RERANK_ENABLED` 开关，对已授权 RRF 前 20 个候选重排后取 5 个。默认关闭；暂时不可用时回退 RRF 顺序并记录降级，权限失败和伪造候选直接报错。Cohere `rerank-v3.5` 适配器通过离线 HTTP 契约检查，**真实接口与效果尚未验证**，现有问答未切换策略。

在 `backend` 目录执行：

```powershell
uv run --locked python -m app.evaluate_rerank
uv run --locked pytest -q tests/test_reranker.py tests/test_reranked.py tests/test_rerank_evaluation.py
```

默认评测仅预检；真实比较需人工复核 dev、真实索引和 API 配置后显式 `--run-real`。本次 15 题 fake 诊断不产生正式效果数字。配置、单次真实验证命令、配对评测与默认关闭理由见 [重排及实验记录](docs/rerank.md)。


## 数据库入库任务与单 worker（第 20A / 20B 步）

API 接受上传后返回 `202`，独立 worker 领取 queued 任务并调用原入库服务。显式重新入库：`POST /knowledge-bases/{kb_id}/documents/{document_id}/ingestions`；管理员查询 `GET /knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job_id}`。同一文档最多一个 queued/running 任务，重复上传连已结束的最近任务也复用；需要重建时显式请求入库。

API 与 worker 使用相同数据库、私有上传目录。先停止旧版 worker，在 `backend` 中执行迁移，再在另一个已配置环境的 PowerShell 终端启动唯一新版 worker：

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m app.worker
```

本地 fake 演示应在 **API 启动前** 设置 `$env:RETRIEVAL_EMBEDDING_BACKEND = "fake"`，且使用独立 fake 知识库。任务记录模型配置快照；worker 按快照执行，缺少真实密钥会明确失败，不自动换成 fake。单任务调试可运行 `uv run --locked python -m app.worker --once`，但不能与常驻 worker 同时运行。

第 20B 步增加默认 60 秒租约、10 秒心跳与 run_token：过期任务可重领，默认最多 3 次；永久错误直接终结。发布与任务成功在同一事务提交，旧执行不能覆盖新结果。仍按单 worker 部署，外部模型调用可能重复，**不保证端到端 exactly-once**。完整 PowerShell 演示、任务字段、错误码、测试和一致性边界见 [任务与 worker 说明](docs/ingestion_jobs.md)。


故障恢复验收（需配置临时测试服务器）：

```powershell
uv run --locked pytest -q tests/test_job_recovery.py
```

测试使用可控时钟和事件验证崩溃、延迟返回、租约耗尽和删除边界，不等待真实租约到期。`--lease-seconds` 和 `--heartbeat-seconds` 可调整租约及心跳间隔；旧版无令牌检查的 worker 必须在迁移前停止。

## 文档删除与原文件重建（第 21 步）

管理员可调用 `DELETE /knowledge-bases/{kb_id}/documents/{document_id}` 删除文档，或 `POST /knowledge-bases/{kb_id}/documents/{document_id}/rebuild` 返回 202 入队重建。删除在同一事务撤销有效构建、取消活动任务；旧 worker 不能重新发布。重建失败保留旧索引，成功才原子切换，旧构建引用返回 410 SOURCE_EXPIRED。模型/维度不兼容时明确拒绝，不覆盖原文件。

文件清理限定私有上传目录，响应包含 cleanup_status。**Windows 原生环境目前仅逻辑删除，原文件清理为 pending；Linux/WSL 支持受目录句柄保护的清理。** 历史块暂保留作诊断，不进入检索。

在 backend 目录、配置临时测试服务器后执行：

```powershell
uv run --locked pytest -q tests/test_document_lifecycle.py
```

接口、错误码、并发顺序、完整 PowerShell 演示与验收见 [文档生命周期说明](docs/document_lifecycle.md)。

## 请求级 trace（第 22 步）

先执行 `uv run --locked alembic upgrade head`，再重启 API。每次问答保存精简 trace 到现有 PostgreSQL，不需要额外监控平台。用回答或错误响应的 request_id 查看 `GET /knowledge-bases/{kb_id}/traces/{request_id}`：仅当前仍在库内的请求本人或管理员可读。

trace 包含策略、阶段/总耗时、候选及引用 ID、模型计数/usage 和失败类型；默认不保存问题、提示词、答案或原文。成本只有在配置有效的 `TRACE_PRICES` 且 usage 完整时才估算，否则为 null。`X-Trace-Status=unavailable` 明确表示本次未能保存。

PowerShell 命令、价格格式、事件结构及一次实际离线请求的耗时分析见 [trace 说明](docs/traces.md)。验收：在 backend 和已配置的临时数据库环境执行 `uv run --locked pytest -q tests/test_traces.py`。

## 只读 Agent 工具（第 23 步）

`app.agent.tools.KnowledgeTools` 提供 `search_knowledge(query, top_k)` 和 `read_chunks(chunk_ids)`；后端每个提问创建独立实例并注入只读身份与知识库上下文。每次调用重新鉴权，read 只读取最近一次检索实际返回且仍有效的块。参数 schema、返回状态、正文与序列化预算见 [工具说明](docs/agent_tools.md)。本步没有 Agent 循环或新的 HTTP 接口。

在 backend 目录配置专用 `TEST_POSTGRES_ADMIN_URL` 后验收：

```powershell
uv run --locked pytest -q tests/test_agent_tools.py
```

测试使用真实临时 PostgreSQL 与 fake Embedding，不代表真实检索或 Agent 效果。下一步编排须由新的编号任务启动。

## 单次工具决策图（第 24A 步）

`app.agent.graph.run_once` 使用 LangGraph 让模型决定零次或一次只读工具调用，然后复用已有证据与引用校验生成结果。没有回边或自动循环；身份从后端 runtime context 注入。新增原生 OpenAI tool calling 适配器和 fake 决策模型，真实接口尚未验证。

先在 backend 执行 `uv sync --locked`，配置专用 `TEST_POSTGRES_ADMIN_URL` 后验收：

```powershell
uv run --locked pytest -q tests/test_agent_graph.py tests/test_agent_tools.py tests/test_answers.py
```

状态字段、读取分支的前置候选、deadline 边界和后端调用示例见 [单次图说明](docs/agent_graph.md)。现有 HTTP 问答未切换成 Agent；本步不提供多轮自主检索或效果提升结论。

## 有预算的再次检索（第 24B 步）

`app.agent.loop.run_agent` 允许模型根据工具结果改写查询、补读、澄清或结束。程序最多执行 3 次工具、6 次模型请求（含 Embedding、重试与最终生成），默认 60 秒；限制累计工具上下文并拒绝相同调用。返回 termination_reason，最终复核权限、来源和引用。

在 backend、配置专用测试数据库后运行：

```powershell
uv run --locked pytest -q tests/test_agent_loop.py
uv run --locked pytest -q -s tests/test_agent_loop.py::test_failed_retrieval_result_drives_rewrite_then_success
```

第二条命令打印真实执行的 fake 决策轨迹，不产生模型费用。预算与网络取消边界、后端工厂示例、查询记录规则见 [循环 Agent 说明](docs/agent_loop.md)。真实模型接口与检索效果尚未验证。

## 固定 RAG 与 Agent 的配对评测（第 25 步）

默认只预检已复核条件，不发出 API 请求、不增加工具或改变检索：

```powershell
cd backend
uv run --locked python -m app.evaluate_agent
```

输出逐题 JSONL、配置/索引哈希、统计报告和人工复核模板。当前 dev 15 题仍为 draft，真实对比未运行；退出码 2 和 null 指标如实表示阻塞/未测量。真实运行、费用预算、独立复核和回归命令见 [Agent 评测与安全审查](docs/agent_evaluation.md)。引用合法仍可能伴随错误事实，不能据此报告正确率。

## 前端登录与知识库选择（第 26A 步）

后端按上文启动后，另开终端，从仓库根目录执行（Node.js >=22.12，建议 24）：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`，使用已创建的演示账号登录。页面展示当前用户、后端授权的知识库和单库选择；令牌只存内存，刷新后需重新登录，过期或受保护接口返回 401 时自动回到登录页。第 26A 步仅提供认证；文档管理见下方第 26B 步，单轮问答见下方第 27 步。

前端 `/api` 默认代理到本机后端 8000 端口，不需要模型密钥。首次空列表的处理、PowerShell 启动步骤、令牌取舍和真实浏览器验收见 [前端认证说明](docs/frontend_auth.md)。

## 文档管理页面（第 26B 步）

登录并选择知识库后点击“打开文档”：管理员上传、查看任务状态和失败原因、预览切块、重建与确认删除；成员只读列表和预览。支持分页、空状态、处理中及失败提示。上传限制为 MD/TXT/文本型 PDF、单文件 10 MiB。

启动前端和 API 后，在使用相同数据库、上传目录及后端模型配置的另一个终端启动唯一 worker：

```powershell
cd backend
uv run --locked python -m app.worker
```

任务按顺序轮询，完成/失败/错误/切库/离开时停止，单轮最多 60 次且不超过 2 分钟；可手动重新检查。预览只读当前已发布构建，最多 3 块 × 600 字，后端重新检查成员权限。重建失败保留旧索引。完整 PowerShell 启动、接口、fake 与真实后端验收命令见 [文档管理说明](docs/frontend_documents.md)。

## 单轮问答界面（第 27 步）

选择知识库后点击“开始问答”，选择固定 RAG（默认）或 Agent。展示后端校验后的状态、Markdown 回答与引用；点击引用重新授权读取文档名、页码/标题和原文。Agent 在请求完成后展示安全工具事件与结束原因。资料不足、澄清、超时和权限失效分别显示。

每次只提交当前问题，没有多轮记忆。切库清空输入、回答与来源，取消旧请求；采用普通 HTTP，没有 SSE 或逐 token 流式生成。Markdown 关闭原始 HTML，来源原文按纯文本展示。启动前端前重新执行 `npm ci` 安装锁定的 react-markdown。

问答仍需后端真实模型配置，失败不自动用 fake 替代；真实 API/数据库与 fake 模型联调结果、PowerShell 验收命令和接口示例见 [单轮问答说明](docs/frontend_question.md)。
