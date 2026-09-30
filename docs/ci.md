# CI 与关键回归（第 29 步）

## 执行范围

`.github/workflows/ci.yml` 对 push、pull_request、workflow_dispatch 执行两个独立 Ubuntu 24.04 job。没有发布/部署步骤、生产密钥或 `pull_request_target`。GitHub Actions 上的实际状态须以仓库运行记录为准，本地通过不等于已经在 GitHub 执行。

| Job | 必跑检查 | 外部依赖 |
| --- | --- | --- |
| backend | Ruff 静态/格式，所有非浏览器单元与数据库回归，包括迁移、pgvector、权限、Agent 预算和 worker fencing | 临时 PostgreSQL 17 + pgvector 0.8.6 |
| frontend-e2e | npm ci、TypeScript、Prettier、Vite build、模拟 HTTP 浏览器回归，以及真实 API/worker/数据库的浏览器闭环 | Chromium、独立临时 PostgreSQL+pgvector；模型只有 fake |

固定 Python 3.12.13、Node 24.21.0、uv 0.12.15；沿用两份依赖锁。Actions 固定官方标签对应完整 commit，数据库镜像固定摘要，与 Compose 一致。每个 job 最多 15 分钟；同分支新提交取消旧 CI。worker 只有一个，浏览器 workers=1、retries=0。

CI 数据库里的 `ci-disposable-only` 是仅用于 GitHub 临时服务的测试密码，不是应用默认密码。夹具创建随机数据库并在 finally 删除。服务失败、缺 TEST_POSTGRES_ADMIN_URL、不是 PostgreSQL 或缺少 vector 扩展时，CI 入口报错，不能跳过后显示绿色。CI 选择范围内任何 skip 都使 job 失败；两个收费烟测是明确 deselected，浏览器与非浏览器套件也是明确分开，报告不会混作通过。

## 覆盖映射

| 用户场景 | 执行的断言/测试 |
| --- | --- |
| 登录→上传→入库→提问→引用 | `test_frontend_keyflow.py` → `keyflow-real.spec.ts`；浏览器真实登录上传，独立 worker 处理，回答 citation.document_id 必须是刚上传的文档，点击来源检查正文；同题检查固定/Agent |
| 删除后不可检索 | 同一浏览器测试删除后 search.items=[]、旧引用/下载 404、固定及 Agent 返回 insufficient_evidence；`test_document_lifecycle.py::test_delete_hides_every_read_and_allows_new_upload` 另覆盖向量/BM25 |
| 普通成员不可管理 | keyflow 使用 carol 实际登录 token 发上传/删除/重建，必须 403；不能仅验证按钮隐藏；已有文档浏览器回归同时覆盖只读页面 |
| 跨知识库访问 | keyflow 中 B 管理员访问 A 的 search/source/raw/job 均被拒绝，且把 A 文档/构建/块/任务 ID 放进有权访问的 B 路径仍 404；B 搜索保持空，不能泄露 A 的片段 |
| 无资料拒答 | B 空库及删除后的 A 分别检查 insufficient_evidence；已有 `test_answers.py` 检查空检索不调用 Chat |
| Agent 预算停止 | `test_agent_loop.py`：三工具上限、model_budget、禁止第七次模型请求、重复调用、可控截止时间和上下文上限；全部在 backend job 必跑 |
| 旧 worker 不覆盖新执行 | `test_job_recovery.py::test_old_worker_delayed_result_cannot_overwrite_new_success` 两个分支：旧执行迟到成功/失败均 lease_lost，新 active_build_id/run_token 保留且旧块不发布；Event + fake clock 控制顺序 |

fake 只替代模型；JWT/Argon2、文件存储、解析切块、SQL/pgvector、服务和前端请求使用真实实现。新增闭环没有 Playwright route 拦截。模型结果固定，验证工程行为，不统计真实 RAG 正确率。

## 本地执行与 CI 相同命令

需要 Python/uv、Node、Docker 和 Chromium。仓库根目录先启动**专用测试容器**，不要把 TEST_POSTGRES_ADMIN_URL 指向现有用户数据库；数据库用户需要创建临时库的权限。

PowerShell 示例（随机测试密码只保存在当前进程环境；端口 55429 需空闲）：

```powershell
$bytes = New-Object byte[] 24
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$env:POSTGRES_PASSWORD = ([BitConverter]::ToString($bytes)).Replace('-', '')
docker run --rm -d --name ragdesk-ci-local -e POSTGRES_PASSWORD -p 127.0.0.1:55429:5432 --health-cmd "pg_isready -U postgres" --health-interval 2s --health-timeout 3s --health-retries 15 pgvector/pgvector:0.8.6-pg17-trixie@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6
$env:TEST_POSTGRES_ADMIN_URL = "postgresql+psycopg://postgres:$($env:POSTGRES_PASSWORD)@127.0.0.1:55429/postgres"
Remove-Item Env:OPENAI_API_KEY, Env:COHERE_API_KEY -ErrorAction SilentlyContinue
$env:RUN_REAL_RETRIEVAL = '0'
$env:RUN_REAL_RERANK = '0'
# 等待 docker inspect --format '{{.State.Health.Status}}' ragdesk-ci-local 返回 healthy。
Set-Location backend
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest --ci-suite backend -q --junitxml=../artifacts/ci/backend.xml
Set-Location ../frontend
npm ci
npm run typecheck
npm run format:check
npm run build
npx --no-install playwright install chromium
npm test
Set-Location ../backend
$env:RUN_FRONTEND_E2E = '1'
uv run --locked pytest --ci-suite e2e -q --junitxml=../artifacts/ci/e2e.xml
Set-Location ..
docker stop ragdesk-ci-local
Remove-Item Env:TEST_POSTGRES_ADMIN_URL, Env:POSTGRES_PASSWORD, Env:RUN_FRONTEND_E2E
```

Linux CI 用 `playwright install --with-deps chromium` 安装浏览器系统库。WSL 的 Docker CLI 路径依实际环境调整。发生失败先保存 JUnit/日志，再停止测试容器；不要用多次随机重跑掩盖失败。浏览器服务启动和请求等待均有截止；不启动另一份占用 5173 的 Vite。

## 本步发现并修复的测试问题

- 完整后端回归暴露重建途中引用请求偶发 401。带诊断复现确认同一令牌先验证成功，随后出现 `ImmatureSignatureError`，iat 比当前验证时间超前约 1.69 秒；测试期间宿主墙上时钟回退。进程内测试现在通过 fixture 为签发和 PyJWT 校验提供同一个可控时钟，保留 datetime 类型识别。额外测试明确验证未来 iat 与到期 exp 仍拒绝。没有关闭校验或增加生产 token 容忍窗口；真实浏览器/API 子进程保留实际时钟，部署主机仍需正确同步时间。
- 浏览器契约测试的 `getByText` 同时命中输入框和已提交问题，触发 strict mode violation。限定为“问答结果”区域内的文本，继续验证独立请求，不修改页面行为、不增加重试。

## 不收费的边界与产物

自动 CI 不读任何模型 Secrets，OPENAI_API_KEY/COHERE_API_KEY 显式为空，真实模型开关关闭。`--ci-suite` 入口发现非空密钥会拒绝启动，并限制测试进程 HTTPX 默认网络传输仅访问 loopback；HTTPX MockTransport 协议模拟不受影响。浏览器测试子进程清除模型密钥并显式注入 fake，worker 只处理 fake 配置索引。此保护不是操作系统级网络沙箱；安装包/浏览器仍需联网。

上传产物仅限 JUnit 和第 29 步模拟测试文本日志，保留 7 天，不上传 .env、数据库、原文件目录或含请求头的浏览器 trace/video。现有浏览器测试在本机生成的演示截图仍在 Git 忽略的 artifacts 中，不作为默认 CI 上传内容。

## 真实模型检查：独立手动执行

不在本 CI workflow 中启用；继续使用既有显式 opt-in 烟测。操作者自行提供环境变量，确认费用后在**独立终端**运行，不加 `--ci-suite`，不修改自动 CI：

```powershell
Set-Location backend
# 先在当前环境设置自己的 OPENAI_API_KEY 与专用 TEST_POSTGRES_ADMIN_URL。
if (-not $env:OPENAI_API_KEY) { throw '需要自己的 OPENAI_API_KEY' }
$env:RUN_REAL_RETRIEVAL = '1'
uv run --locked pytest -q -rs tests/test_retrieval.py::test_real_semantic_retrieval_opt_in
Remove-Item Env:RUN_REAL_RETRIEVAL
# 如另行验证 Cohere，提供 COHERE_API_KEY，再显式启用该烟测。
if (-not $env:COHERE_API_KEY) { throw '需要自己的 COHERE_API_KEY' }
$env:RUN_REAL_RERANK = '1'
uv run --locked pytest -q -rs tests/test_reranker.py::test_real_cohere_chinese_contract
Remove-Item Env:RUN_REAL_RERANK
```

缺配置时的 skipped 不能当作已验证。真实结果单独记录提供商、调用数和日期，不和 fake 统计合并；本步不调用收费接口。这些现有测试是接口烟测，不替代人工复核后的 dev 效果评测。

## 官方依据

- [GitHub PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- [uv GitHub Actions 集成](https://docs.astral.sh/uv/guides/integration/github/)
- [Playwright CI](https://playwright.dev/docs/ci)
- 固定 Action 的官方发布：[checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)、[setup-node v7.0.0](https://github.com/actions/setup-node/releases/tag/v7.0.0)、[setup-uv v10.2.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0)、[upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1)。
