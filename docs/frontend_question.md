# 单轮问答界面（第 27 步）

## 使用

登录、选择知识库，点击“开始问答”。默认固定 RAG，可选 Agent；输入 1～4000 字的问题后提交。页面只保留当前一轮结果，下一次提交不发送之前的问题/答案。切换模式清空回答/引用/事件；切换知识库还清空输入问题。单库授权边界由后端执行。

| 页面结果 | 含义 |
| --- | --- |
| 已回答 | 后端已通过结构、权限、来源有效性及引用 ID 校验，仍不保证事实结论完全受证据支持 |
| 资料不足 | 没有足够的授权证据，未伪造成功答案 |
| 需要补充信息 | 当前问题缺少条件；补充后作为新的独立问题提交 |
| 超时/校验失败/服务故障 | 技术错误，不展示为资料不足或已回答 |
| 权限失效/来源不可访问 | 返回知识库重新选择；401 自动回登录页 |
| 旧构建引用失效 | 来源接口返回 410，不把旧引用映射到新文本，重新提问 |

点击下方 `[c1]` 等来源卡片重新请求后端来源接口，显示实际文档名、页码/标题/行号、原文片段。正文标记仍保留，但不会根据正文生成未经后端验证的来源链接。再次读取失败时清空上次打开的原文，不用缓存片段掩盖删除、重建或撤权。

Agent 事件在普通请求结束后一次展示：搜索/补读、查询摘要、状态、结果数、实际工具/模型请求计数和结束原因。不是实时进度，也不是逐 token 流式输出。沿用 24B 最多 3 工具/6 模型请求/60 秒的限制；没有新增工具、循环策略、跨请求记忆或 SSE。

## 启动（PowerShell）

数据库、迁移、账号和资料入库沿用 README 与[文档管理说明](frontend_documents.md)。配置必须在后端进程环境中提供，前端不保存任何模型密钥。

终端一，从仓库根目录启动后端（已配置 DATABASE_URL、JWT_SECRET、模型设置）：

```powershell
cd backend
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

终端二，从仓库根目录启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。已有资料必须完成入库；新上传仍需按第 26B 步启动唯一 worker。真实问答需要后端 OPENAI_API_KEY，Chat/决策使用现有 CHAT_MODEL；Embedding 与有效索引配置须兼容。缺少模型配置返回明确错误，**不会自动换用 fake Chat/决策**。

普通请求不自动重试。问答客户端等待最多 75 秒，为默认 60 秒 Agent 与响应开销留余量；其他请求仍 15 秒。切库/模式/离开会取消客户端等待并忽略迟到结果，但不承诺取消供应商已处理或已计费的请求。固定 RAG 的原有模型重试/超时保持不变，浏览器超时不代表后端已停止运行。

## 接口契约

`POST /knowledge-bases/{kb_id}/answers`，Bearer 身份，前端经同源 `/api` 代理：

```json
{"question": "报销需要在几天内提交？", "mode": "agent"}
```

`mode` 为 `rag|agent`，省略时默认 rag，原固定 RAG 调用保持兼容。`top_k` 的原 1～20 配置用于固定模式；Agent 自主选择既有限制内的候选数，传非默认 top_k 返回 422。额外 user_id、history、工具参数或预算字段均拒绝，不向模型开放身份。

响应示例仅为结构示意，不是实际效果结果：

```json
{
  "status": "answered",
  "answer": "须在 7 天内提交。[c1]",
  "citations": [{
    "citation_id": "c1", "document_id": "<uuid>", "build_id": "<uuid>",
    "chunk_id": "<uuid>", "document_name": "演示规则.md",
    "snippet": "演示数据：须在 7 天内提交。", "page_number": null,
    "heading_path": ["报销规则"], "start_line": 3, "end_line": 3,
    "source_path": "/knowledge-bases/<kb>/sources/<document>/<build>/<chunk>"
  }],
  "request_id": "<server-generated-id>",
  "mode": "agent",
  "events": [{
    "step": 1, "tool": "search_knowledge", "status": "success",
    "query_summary": "报销提交期限", "result_count": 1
  }],
  "termination_reason": "model_finished",
  "tool_call_count": 1,
  "model_call_count": 4
}
```

固定模式的 events 为空、termination_reason 和两个 Agent 计数字段为 null。Agent 事件最多 3 条，查询摘要最多 160 字；没有完整工具原文、内部状态、提示词或思维链。技术错误仍使用错误 HTTP 状态与 `{error,request_id}`，可附相同安全 Agent 摘要；权限/证据失效错误不返回此前工具事件。

API 只是把认证上下文注入现有 run_agent。最终结果沿用 AnswerResult/引用校验，附加结果由服务层投影；返回前再次校验成员与引用有效性。Agent trace 合并进现有受保护 trace 存储；监督器超时只记录已知尝试数、标记不完整，不把尚未完成的模型 usage/调用观测填成零。价格仅依有效价格配置和完整 usage 估算。没有新数据库迁移。

## 安全渲染与取舍

- 使用 `react-markdown@10.1.0` 和 `skipHtml`，不安装原始 HTML 插件、不使用 `dangerouslySetInnerHTML`。支持基础 CommonMark 标题、列表、强调和代码；本步没有额外 GFM/表格/语法高亮插件。
- 普通 Markdown 链接和图片显示为文本，不访问模型给出的 URL。引用卡片来自后端 citations 数组；请求路径由当前库和三个已验证 UUID 构造，不盲目信任 `source_path`。
- 原文片段使用纯文本。前端类型/响应形状检查只用于避免误展示，后端引用与权限校验仍是依据。
- 每次问题对应新的 request_id、工具上下文和预算。没有 UI 历史持久化，也没有服务端跨轮记忆。

依赖已查看[组件官方文档](https://github.com/remarkjs/react-markdown)，peer 要求 React/类型 >=18，兼容已有 React 19.3。固定 10.1.0 与完整传递依赖，83 个新增锁条目；既有包版本没有升级。生命周期参考 [React useEffect](https://react.dev/reference/react/useEffect)，接口过滤参考 [FastAPI response model](https://fastapi.tiangolo.com/tutorial/response-model/)。

## 验收

浏览器控制 HTTP/时间的测试：

```powershell
cd frontend
npm ci
npx playwright install chromium
npm run build
npm run format:check
npm test
```

真实 PostgreSQL、FastAPI/JWT、Agent 循环与浏览器，模型**仅在测试工厂中**注入 fake。先安装前端依赖和 Chromium，再从仓库根目录执行；使用可建/删库的专用测试实例，不指向已有用户数据。

```powershell
cd backend
$env:TEST_POSTGRES_ADMIN_URL = "postgresql+psycopg://测试账号:测试密码@127.0.0.1:测试端口/postgres"
$env:RUN_FRONTEND_E2E = "1"
uv run --locked pytest -q tests/test_agent_http.py tests/test_answers.py tests/test_traces.py tests/test_agent_loop.py tests/test_frontend_question.py
```

测试夹具创建随机数据库和临时测试应用，结束清理数据库/进程；本机 5173 端口须空闲。不保存浏览器 trace/视频或令牌，安全日志与模拟资料截图位于忽略目录 `artifacts/validation/step27/`。实际结果见[进度](progress.md)。没有运行真实模型接口/效果实验；自动浏览器仅 Chromium，PowerShell 命令未在 Windows 原生验证。

## 知识点与面试追问

**知识点：数据与可执行内容的边界。** 模型返回的是不可信数据。把 Markdown 转成受控 React 元素、关闭原始 HTML，并把引用入口绑定后端数据，可以避免把字符串当成脚本或未经授权的 URL 执行。

1. **为何点击引用还要请求后端？** 回答返回后可能撤权、删除或重建；重新校验当前成员和 document/build/chunk 链才能显示仍有效的来源，410/404 不回退缓存。
2. **为何界面连续提问不是多轮记忆？** 每次 HTTP 只发当前 question/mode，后端创建新的 RunContext、证据集合和预算，没有传递历史。澄清后的补充也必须作为完整的新问题提交。
