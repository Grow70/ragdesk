# 三分钟演示脚本（第 31 步）

**模式声明**：本脚本演示本地 fake 工程闭环，再回放一条可控 Agent 回归轨迹。不是现场真实模型效果演示。三分钟是讲解安排，不是系统延迟承诺；本步未实操计时排练。

## 计时前准备

1. 按 [README 的 PowerShell Compose 步骤](../README.md) 完成构建、显式迁移、`alice` 初始化并启动服务，确认 `/api/health/ready` 成功。用 fake 独立卷，不切换为真实模型。
2. 按 README 登录取得管理员 `$headers`，创建两个库：“演示 A”（本轮上传用，保持空）和“空库”（拒答用）。知识库创建目前通过 API，不承诺页面有建库按钮。准备 `data/sample_docs/a/A-EXP-001.md`，不要使用真实内部文件。
3. 另建演示用户 `bob`，只加入自己创建的 B 库；不把 bob 加入 A。用独立浏览器会话或终端保存 bob 的令牌，避免切换时刷新丢失 alice 内存令牌。只用自选密码，不录入镜头或提交。
4. 预先打开文档页、空库标签页、终端和下面的 Agent 轨迹回放。若已有预入库演示文档，显著标明“预置资料”；不要将其当成本次上传成功。

初始化第二个用户（仓库根目录；已存在则不重复初始化）：

```powershell
docker compose run --rm --no-deps backend init-demo --login-name bob --display-name Bob
$api = 'http://127.0.0.1:8080/api'
$credential = Get-Credential -UserName bob -Message '输入刚初始化的演示密码'
$body = @{ login_name = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json
$bobSession = Invoke-RestMethod -Method Post -Uri "$api/auth/session" -ContentType application/json -Body $body
Remove-Variable credential, body
$bobHeaders = @{ Authorization = "Bearer $($bobSession.access_token)" }
$bobKb = Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases" -Headers $bobHeaders -ContentType application/json -Body '{"name":"B"}'
# $headers 是按 README 登录 alice 获得的；首次演示创建独立新库。
$demoKb = Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases" -Headers $headers -ContentType application/json -Body '{"name":"Demo A"}'
$emptyKb = Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases" -Headers $headers -ContentType application/json -Body '{"name":"Empty"}'
```

创建完成后重新登录/刷新知识库列表。若需普通成员只读演示，按 README 成员 API 加入第三个用户；不纳入三分钟主线。不要输出令牌变量、请求头或 `.env`。

## 三分钟主线（累计 180 秒）

| 时间 | 操作与观察 | 建议口述 |
| --- | --- | --- |
| 00:00–00:20 | alice 登录，选 Demo A；展示管理员身份 | “这是模拟资料项目，本次模型是 fake，用于展示真实前后端、数据库和 worker 的工程流程。” |
| 00:20–00:45 | 上传 A-EXP-001.md；查看任务 ID 和 queued/running/succeeded | “上传接受返回 202，独立 worker 解析、切块、嵌入；所有块成功才发布。上传完成不等于已可检索。” |
| 00:45–01:10 | 任务 succeeded 后选固定 RAG，问“报销期限是多少？”；看状态和 FAKE 标记 | “后端只检索当前授权库。fake 回显片段，不保证这段恰好回答问题；真实答案质量尚未评测。” |
| 01:10–01:30 | 点击引用，查看文档名、标题/行号、片段 | “引用元数据来自后端真实块。ID 有效证明来源可访问，是否支持结论还要对照原文。” |
| 01:30–01:50 | 切换 Empty，问“报销期限是多少？”；展示 insufficient_evidence | “空库没有证据，后端直接返回不足，不调用 Chat。非空库里的未知事实需要另做真实模型评测。” |
| 01:50–02:15 | 终端用 bob 令牌直接请求 A 的 search，展示 404；对自己 B 可正常查询空结果 | “直接改 kb_id 也过不了后端权限。404 统一处理无权和不存在；界面隐藏入口不是安全边界。” |
| 02:15–02:50 | 切回 Demo A 的 Agent 模式展示事件入口；随后切终端回放下方 fake 测试轨迹 | “默认 fake Agent 仅搜索一次。这里是另一条可控测试记录：首次工具超时，下一决策改写查询，再检索取得证据；两次工具、六次模型请求，含 Embedding 和最终生成。不是实时真实模型决策。” |
| 02:50–03:00 | 展示最终报告“未执行”及限制 | “真实模型效果尚无结论。我完成的是可验证工程流程、授权与终止机制；不会把 fake 测试当成准确率。” |

若任务未按时间窗口完成，展示真实当前状态并说明“仍在处理”，切到标记清楚的预置成功案例或结束该片段；若失败展示错误，不把失败包装成成功。正常返回前后端均为普通请求，不称逐 token 流式输出。

### 权限隔离现场命令

使用准备时的变量；错误预期是 HTTP 404，其他网络/服务错误不能算隔离通过：

```powershell
try {
    Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases/$($demoKb.id)/search" -Headers $bobHeaders -ContentType application/json -Body '{"query":"demo","top_k":5}'
    throw '意外成功：停止演示并排查权限'
} catch {
    if (-not $_.Exception.Response) { throw }
    $status = [int]$_.Exception.Response.StatusCode
    Write-Host "bob 访问 A：HTTP $status"
    if ($status -ne 404) { throw }
}
Invoke-RestMethod -Method Post -Uri "$api/knowledge-bases/$($bobKb.id)/search" -Headers $bobHeaders -ContentType application/json -Body '{"query":"demo","top_k":5}'
```

B 自己的库预期返回空 items；其他库不能出现 A 的内容。自动化还检查了跨库引用/下载/任务和普通成员管理拒绝，见 [CI 覆盖表](ci.md)。

### Agent 补充检索：明确标注的历史回放

仓库根目录执行；仅显示已核实的保存结果，不启动模型：

```powershell
$archive = Get-Content reports/evidence/step31/engineering-validation.json -Raw -Encoding UTF8 | ConvertFrom-Json
$record = $archive.entries | Where-Object { $_.source -eq 'artifacts/validation/step24b/demo.txt' }
Write-Host '历史 fake 回归轨迹，不是当前请求'
$record.trace | ConvertTo-Json -Depth 12
```

原记录：`报销规则` → `MODEL_TIMEOUT` → `审批期限 680 CNY` → 1 个候选 → answered；2 次工具调用、6 次模型请求。query、金额和输出由测试夹具固定，不能声称模型自行推理出了改写。模型预算同时覆盖决策、Embedding、重试与最终 Chat；次数不等于全部是 Chat。

要现场重跑同一验证，先按 [CI 文档](ci.md) 启动**专用测试数据库**并设置环境，然后在 `backend` 目录执行（已有锁定依赖）：

```powershell
uv run --locked pytest --ci-suite backend -q -s tests/test_agent_loop.py::test_failed_retrieval_result_drives_rewrite_then_success
uv run --locked pytest --ci-suite backend -q tests/test_agent_loop.py::test_search_then_read_then_final_answer
```

第一项打印新 trace，第二项验证搜索后补读再作答。缺数据库时命令失败，不把跳过说成通过；数据库和权限实现真实，模型固定输出。不要为了演示临时调用付费 API 或更改程序决策。
