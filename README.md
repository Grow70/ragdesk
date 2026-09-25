# Ragdesk

面向模拟企业资料的知识库问答系统。当前完成后端骨架、核心数据库表、演示用户身份认证、知识库与成员权限，以及原文件上传和受保护读取。支持 `/health/live`、`/auth/session`、`/auth/me`、`/knowledge-bases` 和文档接口；尚无文件解析、检索、问答或模型调用。

需求和后续实现契约分别见 [docs/requirements.md](docs/requirements.md) 与 [docs/architecture.md](docs/architecture.md)。

## Windows PowerShell 启动

前提：安装 Python 3.12、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Docker Desktop，并确保 Docker Desktop 已启动。以下命令从仓库根目录执行；本地演示密码请自行替换，不要提交到 Git。

```powershell
$env:POSTGRES_PASSWORD = "change-this-local-password"
docker compose up -d db
docker compose ps db

$env:DATABASE_URL = "postgresql+psycopg://ragdesk:$($env:POSTGRES_PASSWORD)@127.0.0.1:5432/ragdesk"
$env:MODEL_PROVIDER = "example"
$env:MODEL_NAME = "not-used-yet"
$secretBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($secretBytes)
$rng.Dispose()
$env:JWT_SECRET = [Convert]::ToBase64String($secretBytes)
Set-Location backend
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked python -m app.init_demo_users --login-name alice --display-name Alice
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

初始化脚本会交互式要求输入并确认至少 12 字符的演示密码；不会创建默认账号，也不会在应用启动时自动运行。另开一个 PowerShell 窗口检查健康接口和登录：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
$credential = Get-Credential -UserName alice -Message "Demo login"
$body = @{ login_name = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/session -ContentType application/json -Body $body
Invoke-RestMethod -Uri http://127.0.0.1:8000/auth/me -Headers @{ Authorization = "Bearer $($session.access_token)" }
```

健康接口应返回 `status: ok` 和非空 `request_id`，它只检查 Web 进程。登录成功返回有过期时间的 Bearer JWT；`/auth/me` 返回令牌对应的用户身份。`DATABASE_URL`、`MODEL_PROVIDER`、`MODEL_NAME`、`JWT_SECRET` 必需；`JWT_SECRET` 至少 32 字节，只从环境变量读取。`MODEL_API_KEY` 当前可不设置。配置类不自动加载 `.env`；[backend/.env.example](backend/.env.example) 不包含真实密钥。若数据库密码含 URL 特殊字符，构造 `DATABASE_URL` 时需先做 URL 编码。演示配置和密码不作为生产默认配置；重新生成签名密钥会使旧令牌失效。

## 知识库与成员权限

已登录用户可创建知识库，并自动成为该库管理员。`GET /knowledge-bases` 只返回当前用户加入的库；`GET /knowledge-bases/{kb_id}` 要求成员资格。管理员可通过 `GET /knowledge-bases/{kb_id}/members` 查看成员，使用 `PUT /knowledge-bases/{kb_id}/members/{user_id}` 配合 `{"role":"member"}` 或 `{"role":"admin"}` 添加或调整已有用户，使用 `DELETE` 同路径移除成员。移除或降级最后一位管理员会返回 `409`。普通成员可读取知识库及其文档，上传仅限管理员；文档删除和问答尚无接口。

在上面的登录示例取得 `$session` 后，可创建并查看知识库：

```powershell
$headers = @{ Authorization = "Bearer $($session.access_token)" }
$kb = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/knowledge-bases -Headers $headers -ContentType application/json -Body '{"name":"A"}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/knowledge-bases -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:8000/knowledge-bases/$($kb.id)" -Headers $headers
```

集成测试以三个临时用户和两个独立知识库验证成员隔离。服务端只从已验证的 JWT 取得操作人身份，成员变更提交后，下次请求重新查询数据库权限。

## 原文件上传与读取

管理员可通过 `POST /knowledge-bases/{kb_id}/documents` 的 `file` 表单字段上传 `.md`、`.txt` 或 `.pdf`，单文件最多 10 MiB。服务端检查文本编码或 PDF 的基本头尾结构并计算 SHA-256；同一库中相同有效字节返回已有 `document_id`。新文件返回 `201`、`status: uploaded`；重复文件返回 `200`。`uploaded` 表示仅保存原文件，当前没有解析、构建或可检索内容；PDF 的基础检查也不能证明它含有可提取文本，扫描件将在后续解析步骤提示不支持。

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
