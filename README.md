# Ragdesk

面向模拟企业资料的知识库问答系统。当前完成后端骨架、核心数据库表和演示用户身份认证。支持 `/health/live`、`/auth/session`、`/auth/me`；尚无知识库权限、上传或模型调用。

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
