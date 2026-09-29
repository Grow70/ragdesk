# 第 26A 步：前端认证与知识库选择

## 实现范围与真实接口

新增 React + TypeScript + Vite 单页应用，包括登录页、当前用户、授权知识库列表、单库选择、加载/空状态、错误重试与过期退出。主界面适配桌面和窄屏，不包含文档管理、聊天、注册或令牌刷新。

主要实现文件为 `src/api.ts`、`src/App.tsx`、`src/main.tsx`、`src/styles.css`、`index.html`、`vite.config.ts`。依赖声明、TypeScript 配置和测试另列。没有修改后端业务逻辑或增加数据库迁移。

| 浏览器请求 | Vite 代理后的真实后端路径 | 契约 |
| --- | --- | --- |
| POST `/api/auth/session` | `/auth/session` | JSON `{login_name,password}`；返回 `access_token,token_type,expires_in,request_id` |
| GET `/api/auth/me` | `/auth/me` | Bearer；返回 `user_id,login_name,display_name,request_id` |
| GET `/api/knowledge-bases` | `/knowledge-bases` | Bearer；返回 `items` 和 `request_id`；后端已按成员关系过滤 |
| GET `/api/knowledge-bases/{id}` | `/knowledge-bases/{id}` | 每次选择重新授权；返回 `id,name,role,created_at,request_id` |

架构文档早期规划的 `/api/v1` 尚未启用；本步使用已经实现的接口。Vite 仅代理 `/api`，默认目标 `http://127.0.0.1:8000`。浏览器走同源请求，因此本地开发无须放宽后端 CORS。开发及本地 preview 都绑定 `127.0.0.1`；没有公网部署。

客户端校验成功响应的基本结构，使用固定相对路径、JSON、Bearer、15 秒超时，不自动重试登录。错误按 401/403/404/422/429/5xx、网络故障、超时和坏响应转为中文提示，保留可获得的请求编号；不展示服务端原始异常、请求头或令牌。

## 内存令牌的取舍

- 令牌只保存在客户端实例的私有内存字段，不写 localStorage、sessionStorage、Cookie、URL 或日志。当前用户来自 `/auth/me`，不通过解码未验证 JWT 决定身份。
- 刷新或关闭标签页需要重新登录，各标签页会话独立；这是演示版的明确取舍，不实现“记住我”。密码提交后清空输入框，不放入 React 持久状态或存储。
- `expires_in` 用于设置页面过期提示；按登录请求开始时间计算并保守减去一秒，避免 JWT 秒级时间精度和网络往返令显示的有效期偏长。恢复窗口焦点/页面可见性和发送受保护请求前也检查期限。
- 后端验签和过期检查才是认证依据。任何受保护请求返回 401 都清空用户、知识库、选择和令牌，回到登录页；登录接口 401 只提示账号或密码错误。前端时钟可被修改，不能当安全边界。
- 退出取消进行中的请求，并增加会话代次；旧响应即使迟到也不得恢复旧身份。选择序号防止先发出的库详情覆盖用户后来选中的库。
- 没有后端注销/撤销接口，退出只是清理本地会话，已签发的令牌在过期前仍可能有效。内存存储也不能防住任意同源 XSS；本步不声称是完整生产认证方案。
- 列表角色只是展示。选择详情、文档等后续服务仍需后端授权；不以隐藏按钮代替权限检查。选择时若权限被撤销，移除该候选并显示错误，刷新列表也会清除旧选择。
- 前端不需要任何模型密钥或 JWT 签名密钥。`API_PROXY_TARGET` 是 Vite 进程环境中的后端地址；不要把密钥放入 `VITE_*`。`.env.example` 仅为参考，代理目标通过终端环境设置。

## Windows PowerShell 启动

前提：Node.js 至少 22.12，建议 Node.js 24；后端使用 Python 3.12、uv、Docker Desktop。当前验证环境为 Node.js 24.21.0、npm 11.19.0。安装版本锁在 `frontend/package-lock.json`，用 `npm ci` 重现，不用自动升级到 latest。

### 终端一：后端

从项目根目录按 [README 的 Windows 后端启动步骤](../README.md#windows-powershell-启动) 设置数据库、`DATABASE_URL`、`JWT_SECRET`，执行迁移并初始化自己的演示账号，然后：

```powershell
cd backend
uv sync --locked
uv run --locked alembic upgrade head
# 尚未创建账号时执行一次；交互输入密码，无默认生产密码。
uv run --locked python -m app.init_demo_users --login-name alice --display-name Alice
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

如果演示账号已存在，不重复初始化。此步骤不调用 Chat/Embedding，无须模型 API 密钥。

### 终端二：前端

从项目根目录执行：

```powershell
cd frontend
npm ci
# 默认就是这个地址；更改后重启 Vite。
$env:API_PROXY_TARGET = 'http://127.0.0.1:8000'
npm run dev
```

打开 `http://127.0.0.1:5173`，输入刚才创建的账号密码。`5173` 已占用时 Vite 明确失败，不静默换端口。

首次登录若显示“还没有可访问的知识库”，这是正常授权结果。管理员可通过已有后端接口创建演示库，前端本步不提供创建功能。以下命令在**另一个终端**中执行，不会输出令牌：

```powershell
$credential = Get-Credential -UserName alice -Message '登录本地演示账号'
$body = @{ login_name = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/session `
  -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$headers = @{ Authorization = "Bearer $($session.access_token)" }
$payload = @{ name = '团队演示资料' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/knowledge-bases `
  -Headers $headers -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))
```

回前端点击“刷新列表”，应看到新库且角色为管理员。成员关系管理继续使用 README 中的受保护后端接口。

### 手动验收

1. 输入错误密码，看到统一提示；输入正确密码，看到当前用户和授权库。
2. 点击库卡片，看到当前选择、角色、创建日期；一次只能选一个。
3. 刷新浏览器，回到登录状态；退出登录也清除当前用户和选择。
4. 若要手工观察短期过期，在**后端启动前**设置 `$env:ACCESS_TOKEN_TTL_MINUTES='1'`，重启后重新登录。该值仅用于演示，恢复时移除变量并重启后端。
5. 断开后端，前端显示网络/服务错误；恢复后用“刷新列表”重试。

```powershell
# 前端构建、类型与格式检查
npm run build
npm run format:check
# 本地预览构建，默认 4173；同样需要后端启动
npm run preview
```

静态 dist 部署到其他服务器时，还需同源反向代理 `/api`；Vite 开发代理不是生产部署配置。本步没有进行部署。

## 自动验收

### 浏览器契约测试（模拟 HTTP）

```powershell
cd frontend
npm ci
npx playwright install chromium
npm test
```

使用完整 Chromium 的 headless 模式，测试不启用 trace、视频或含请求头的录制。12 项覆盖正确/错误登录、内存会话/刷新、授权列表/选择、空列表、失败重试和加载、401、可控时钟过期、选择时撤权、迟到请求、网络/响应错误、超时、选择乱序和窄屏溢出。模拟 HTTP 用于固定失败与竞态，不能代替真实后端联调。

### 真实浏览器 + 真实后端 + 临时 PostgreSQL

先完成上面的 Node 依赖和 Chromium 安装。关闭手工启动的 Vite，测试会自行启动并清理它。以下从项目根目录运行：

```powershell
docker run -d --rm --name ragdesk-step26a-check `
  -e POSTGRES_PASSWORD=step26a-test-only -p 127.0.0.1:55446:5432 `
  pgvector/pgvector@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6
# 确认输出 accepting connections 后继续
docker exec ragdesk-step26a-check pg_isready -U postgres
$env:TEST_POSTGRES_ADMIN_URL = 'postgresql+psycopg://postgres:step26a-test-only@127.0.0.1:55446/postgres'
$env:RUN_FRONTEND_E2E = '1'
cd backend
uv run --locked pytest -q tests/test_frontend_auth.py
uv run --locked pytest -q tests/test_auth.py tests/test_knowledge_bases.py
Remove-Item Env:RUN_FRONTEND_E2E
cd ..
docker stop ragdesk-step26a-check
```

pytest 创建随机库，执行已有迁移，建立隔离 A/B 库及演示用户，以随机密码启动真实 FastAPI，并调用 Playwright。结束时清理进程和随机库，不操作既有用户数据库。未显式启用 `RUN_FRONTEND_E2E` 或缺少测试库时会跳过，不能把跳过报告为通过。

真实浏览器三项检查：

- Argon2 密码验证、真实 JWT 登录、`/auth/me`、授权库列表与选择；B 库不出现在列表，另用有效令牌请求 B 的详情由后端返回 404；刷新和退出回到登录页。
- 故障注入只替换一次请求的 Bearer 为**用测试密钥正确签名、exp 已过去**的令牌，不伪造 HTTP 响应；真实后端返回 401，前端清除身份。
- 实际登录获得 `expires_in=60` 后，用 Playwright 时钟推进一分钟，验证前端主动过期。不靠长时间 sleep；此项不修改后端时钟，后端过期验证由上一项负责。

测试就绪检查使用官方支持的 Vite stdout 匹配，避免 WSL 对关闭端口的长时间探测。Vite strictPort 确保不复用他人的服务器。测试环境为 localhost/127.0.0.1 设置 NO_PROXY，不把本机请求发给环境 HTTP 代理。

## 官方文档与版本

- [React 从零构建应用](https://react.dev/learn/build-a-react-app-from-scratch)：采用 Vite 构建客户端应用，本步无需路由/全局状态框架。
- [Vite 入门与 Node 要求](https://vite.dev/guide/)、[开发服务器代理](https://vite.dev/config/server-options)：本步用服务端 proxy，保持浏览器同源。
- [Playwright Clock](https://playwright.dev/docs/clock)、[Web server 的 wait/stdout 配置](https://playwright.dev/docs/test-webserver)：用于可控过期和测试服务器就绪。

本步安装并锁定 React/React DOM 19.3.0、Vite 8.3.1、React 插件 6.1.1、TypeScript 6.0.2、Playwright 1.63.0；完整传递版本见 package-lock.json。没有后端依赖变更。

## 实际验证记录（2026-09-29）

- `npm ci --no-audit --no-fund` 按锁文件重新安装成功；`npm run build`（含 TypeScript 检查）与 `npm run format:check` 通过。
- 最终模拟 HTTP 的浏览器回归：**12 passed（9.6 秒）**，使用真实 Chromium，覆盖页面行为与故障边界；不是后端真实性或模型效果证明。
- 真实 FastAPI + PostgreSQL + 浏览器联调：**3 passed（4.3 秒）**；外层 pytest 夹具 **1 passed（7.57 秒）**，它是同一组检查的启动器，不重复计作第四条浏览器用例。
- 既有后端认证与知识库权限回归：**3 passed（4.14 秒）**。新增 Python 夹具的 ruff check/format 与 git diff --check 通过。本步未执行后端全项目测试。
- 查看了真实登录页和登录后授权库界面截图；390px 窄屏无横向溢出由浏览器用例验证。没有真实模型请求或收费 API 调用。
- 环境处理：初始 WSL Docker 集成不可用，启动 Windows Docker Desktop 后专用数据库可达。Chromium 本体安装成功，额外 FFmpeg 下载因 TLS 连接重置失败；当前不录视频，使用已安装的完整 Chromium 完成全部浏览器测试。
- 初次测试就绪失败来自环境 HTTP 代理对本机请求返回 502；加入 NO_PROXY 后模拟浏览器测试通过。真实联调首次耗时 142.60 秒，检查到关闭的 WSL 转发端口处于 SYN-SENT 等待；改用 Vite stdout 就绪后相同联调 7.57 秒完成。没有因此修改业务逻辑。
- 上次会话末尾的自动审批用量限制使最后一组检查未执行；本次继续后已实际完成，未把拒绝执行当作测试失败或通过。
- 原始日志、登录/工作空间截图保存在 Git 忽略目录 `artifacts/validation/step26a/`。真实联调 fixture 清理随机数据库和进程；专用容器在收尾时停止并自动移除。
