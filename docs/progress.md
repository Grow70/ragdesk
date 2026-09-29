# 项目进度

## 工作规则初始化

- 已完成：建立 `AGENTS.md`，记录项目协作规则和每步交付格式。
- 验证结果：首次使用 `python` 验证未执行（环境中无此命令）；改用 `python3` 后通过内容检查，确认 13 条编号规则、6 项交付要求和 4 个进度栏目。Git 改动范围仅为新建 `AGENTS.md` 和本文件。
- 遗留问题：`docs/requirements.md` 和 `docs/architecture.md` 尚不存在；业务需求和架构尚未定义。
- 下一步入口：等待项目负责人给出下一个编号任务。

## 第 1 步：需求文档

- 已完成：建立 `docs/requirements.md`，定义基础范围与后续阶段、12 项带检查方式的需求、7 个 Given/When/Then 验收场景、明确不做的内容、项目假设、待定配置及性能和效果的测量方法；未编写业务代码。
- 验证结果：`python3` 静态检查通过，确认 12 项需求均有可执行检查描述、7 个场景均包含 Given/When/Then，以及范围和测量章节齐全。产品验收未执行，因为业务功能尚未实现。
- 遗留问题：`docs/architecture.md` 尚不存在；认证方式、文件限额、检索与模型参数等配置待后续任务确定。
- 下一步入口：等待项目负责人给出下一个编号任务，再按本需求文档拆分实现或设计工作。

## 第 2 步：架构契约

- 已完成：建立 `docs/architecture.md`，约定技术边界、两张 Mermaid 流程图、七层职责、五种跨层数据结构、索引构建状态与完整发布规则、授权和错误规则、接口规划、目录建议及 PostgreSQL/pgvector 取舍；未实现业务逻辑或安装依赖。
- 验证结果：已核对 FastAPI、SQLAlchemy、Alembic、pgvector、React 和 LangGraph 官方资料。首次静态检查的代码块数量断言写错而失败；修正检查条件后通过，确认两张 Mermaid 图、七层、五种契约、三种回答状态、定位 ID 与六类接口均存在。未运行产品测试或渲染 Mermaid，因为目前只有文档。
- 遗留问题：认证方案、模型供应商和兼容版本、依赖锁文件、文件限额、检索参数、任务执行方式与 Agent 上限仍需在各自实现任务中确定。
- 下一步入口：等待下一个编号任务；若实现需要改变本文字段、状态、权限或接口，先更新架构契约。

## 第 3 步：可启动后端工程

- 已完成：初始化 `backend/pyproject.toml` 与 `backend/uv.lock`；建立 FastAPI 应用工厂、环境变量配置类、`/health/live`、request_id、基本日志和统一错误响应；按架构建立分层目录；添加不含密钥的 `.env.example`、Git 忽略规则、固定镜像标签及摘要的 PostgreSQL/pgvector Compose、扩展初始化 SQL、最小测试和 Windows PowerShell 启动说明。未实现业务表、登录、上传或模型调用。
- 验证结果：`uv lock` 与 `uv sync --locked` 成功；`pytest -q` 为 3 passed、1 个上游 TestClient 弃用警告；`ruff check .` 和 `ruff format --check .` 通过。实际启动 Uvicorn 后，`/health/live` 返回 200 和 request_id，未知路径返回统一 404 错误体；移除必需配置后进程以明确变量名报错。Compose 数据库实际启动，查询确认 `vector` 扩展版本 0.8.6，随后已停止测试容器并删除本次创建的数据卷。首次测试因导入路径配置缺失而未收集、首次格式检查失败；定位后修复并重跑通过。首次 Compose 配置检查因未设置必需的测试密码失败，设置临时环境变量后通过。
- 遗留问题：健康接口是进程存活检查，不验证数据库或模型；尚未验证真实模型。当前锁定的 FastAPI TestClient 组合有一个弃用警告，后续升级测试客户端时处理。
- 下一步入口：等待下一个编号任务；新增数据库业务、认证、上传或模型调用前按架构契约拆分实现，并先定义相关边界测试。

## 第 4 步：核心数据库模型与迁移

- 已完成：更新架构契约中的表名与向量列时机；建立 `users`、`knowledge_bases`、`kb_members`、`documents`、`document_builds`、`chunks` 六张表的 SQLAlchemy 模型及首个 Alembic 迁移；加入成员唯一性、文件摘要局部唯一索引、必要外键、组合外键、状态与定位检查、普通索引，以及只读取当前有效构建的单库查询函数。未加入向量列或业务接口，应用启动不修改表结构。
- 验证结果：使用专用临时 PostgreSQL 容器运行集成检查，4 个测试通过、1 个既有 TestClient 弃用警告；空库升级和 Alembic 模型差异检查通过，重复成员、重复未删除文件摘要、非法外键及跨文档 active build 指针被数据库拒绝；查询只返回当前库未删除文档的 ready active build，删除后为空；回退至 base 并重新升级通过。测试随机数据库已删除，临时容器已停止。`ruff check .`、`ruff format --check .` 和 `uv lock --check` 均通过。
- 遗留问题：向量列及维度、实际入库流程、认证和授权尚待后续步骤；数据库集成测试需提供 `TEST_POSTGRES_ADMIN_URL`，否则该项测试会明确跳过。
- 下一步入口：等待下一个编号任务；若实现需要调整字段或索引，先更新架构契约并新增 Alembic 迁移。

## 第 5 步：身份认证

- 已完成：在架构契约中确定 `/auth/session` 与 `/auth/me`、Argon2id 和固定 HS256 JWT；新增用户登录名与密码哈希迁移，旧用户记录保留且不可登录；实现显式交互式演示用户初始化、登录、令牌签发与验证、当前用户查询。JWT 密钥从环境变量读取；未知账号与错误密码的登录响应相同；未实现知识库权限或注册等后续能力。更新依赖锁文件、PowerShell 说明和配置示例。
- 验证结果：核对 FastAPI、PyJWT、argon2-cffi 和 SQLAlchemy 官方文档；在专用临时 PostgreSQL 容器中运行 `pytest -q`，5 个测试通过，涵盖正确登录、错误密码与未知账号相同响应、伪造签名、非允许算法、固定过去时间生成的过期令牌、缺失令牌、数据库中的 Argon2id 哈希、旧用户保留、凭据迁移回退和重新升级；测试随机数据库已删除，临时容器已停止。`ruff check .`、`ruff format --check .`、`uv lock --check` 和 `git diff --check` 均通过。首次 Ruff 检查因新测试导入顺序失败，修正后重跑通过。仍有 1 个现有 TestClient 上游弃用警告。
- 遗留问题：未实现知识库角色授权、令牌撤销或刷新；历史无凭据用户需要显式另行设置登录资料才能登录。数据库集成测试需设置 `TEST_POSTGRES_ADMIN_URL`，否则会明确跳过；未做生产部署验证。
- 下一步入口：等待项目负责人给出新的编号任务；需要使用用户身份的业务接口应基于已验证令牌再独立实现知识库授权。

## 第 6 步：知识库与成员权限

- 已完成：更新架构契约；增加知识库创建、按当前成员资格列出、详情读取，以及管理员查看、添加、调整和移除已有用户的成员接口。创建者与知识库在同一事务中成为管理员；`require_kb_member` 和 `require_kb_admin` 作为后续资源接口的复用守卫，每次查询当前数据库成员资格。未知库与非成员返回相同 `404`；普通成员管理操作返回 `403`；移除或降级最后一名管理员返回 `409`。成员变更锁定知识库行并在事务中完成。没有实现文档、检索、引用或任务接口，也未添加依赖。
- 验证结果：核对 FastAPI 依赖、SQLAlchemy 会话与 `with_for_update`、PostgreSQL 行锁官方文档；在专用临时 PostgreSQL 容器运行全量测试，最终 `7 passed`、1 个现有 TestClient 弃用警告。测试建立 A、B 两库和 Alice、Bob、Carol 三名用户，验证列表和详情隔离、伪造库 ID 对成员读取及管理操作均不能越权、成员管理仅管理员可执行、移除后同一 JWT 下次访问失效、最后管理员不可移除或降级；数据库测试库均已删除，容器已停止。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过。首次全量测试有一次 Carol 未授权详情预期 `404` 却收到 `401`；针对该测试复跑及知识库测试连续复跑 10 次、后续全量复跑均通过，原因未确认，未为此作猜测性改动。
- 遗留问题：上传和删除文档的 HTTP 接口尚不存在，因此本步只验证普通成员被 `require_kb_admin` 拒绝，以及其不能操作当前成员管理接口；以后实现文档接口时需补充 HTTP 级验收。首次 `401` 偶发失败仍需在再次出现时保留请求上下文并定位。数据库集成测试未提供 `TEST_POSTGRES_ADMIN_URL` 时会跳过。
- 下一步入口：等待新的编号任务；未来文档、检索、引用和任务入口必须复用本步的成员或管理员守卫，并继续在资源查询中限定知识库 ID。

## 第 7A 步：模拟资料

- 已完成：新增 `data/sample_docs`，编写 8 份短小的中文 Markdown/TXT 虚构资料（A 库 6 份、B 库 2 份），覆盖报销、出行审批、休假、NX-210 产品、接口错误码和运维流程。每份正文首行显著注明“演示数据”，包含稳定的样例文档标识、标题、知识库归属和分段内容；安排两个需综合 A 库不同文档的问题，以及报销金额、提交期限和运维阈值的 A/B 跨库隔离对照。`manifest.json` 记录每份资料的路径、标题、样例文档标识、逻辑知识库标签与 SHA-256。
- 验证结果：执行 Python 标准库静态检查，确认清单覆盖目录内全部 8 份文件、标识唯一、同时包含 Markdown 和 TXT、A/B 两库、每份 UTF-8 正文都有“演示数据”首行及标识/标题/归属/段落，8 个 SHA-256 均与实际文件字节一致。手工核对跨文档问题与 A/B 差异，移除了 B 库正文中原本直接写出的 A 库具体阈值。未执行入库、检索、问答或真实模型效果测试。
- 遗留问题：逻辑知识库标签和样例文档标识不是数据库 UUID；未来导入时需建立实际库与文档 ID 映射。修改任何资料正文后需重算并更新清单哈希。检索隔离与综合问答效果仍待后续实现与验收。
- 下一步入口：等待新的编号任务；未来资料入库和评测可按 `manifest.json` 的归属与 `README.md` 的问题场景建立可复现测试集。

## 第 7B 步：评测集草案

- 已完成：依据第 7A 步语料建立 `data/eval/questions.json`，含直接事实 12 题、同义改写 6 题、跨文档综合 6 题、资料不足 6 题；每条均包含题目、逻辑 `kb_id`、类别、可回答标记、待核事实、稳定文档 ID 加章节和原文摘录、dev/test，以及 `draft` 状态。增加主题 `fact_group`、改写题 `paraphrase_of`、资料不足题的缺失信息和已核对文档范围；dev/test 各 15 题，test 内容摘要单独冻结。生成 `REVIEW.md` 逐条勾选清单，并提供仅用 Python 标准库的 `check_eval_set.py`；未实现或运行检索、问答、模型评测。
- 验证结果：核对 Python `json`、`pathlib`、`hashlib` 官方文档，没有新增依赖或改动锁文件。实际执行检查脚本通过：30 条均为 draft，类别计数 12/6/6/6、dev/test 各 15、无重复 ID，直接题和对应改写题同组同 split，跨文档题至少引用两份本库资料，资料不足题列出本库全部核对文档；样例资料 SHA-256、章节及逐字引文匹配，`REVIEW.md` 与题目文件同步，冻结 test 摘要匹配。用临时修改的内存副本确认脚本能检出缺字段、重复 ID、错误引文、跨库证据、split 泄漏和核对范围不完整。Python 编译检查、Ruff 格式与代码检查、`git diff --check` 通过；未运行模型或产生效果指标。
- 遗留问题：事实推理是否正确、题目是否歧义，以及“资料不足”判断是否成立，都不能由存在性脚本证明，需项目负责人逐条人工复核。当前全部样本为 draft，不可作为正式效果结论。逻辑库标签及样例文档 ID 未来需映射到数据库 UUID；test 不用于调参，人工标注纠错需记录修订缘由并有意识更新冻结摘要。
- 下一步入口：等待新的编号任务；人工复核从 `data/eval/REVIEW.md` 开始，后续真正评测时先确认入库资料版本与本草案证据一致。

## 第 8 步：原文件上传与受保护读取

- 已完成：先更新架构契约，新增无构建时的 `uploaded` 状态及原文件接口；加入锁定的 `python-multipart` 依赖。实现管理员上传 `.md`、`.txt`、`.pdf`（最多 10 MiB）、UTF-8 或 PDF 基础结构检查、SHA-256、同库按有效字节去重、服务端 UUID 私有存储键、写库失败清理文件；实现成员可用的分页文档列表、详情和每次重新授权的原文件下载。文件名仅作展示和下载名，不参与路径；未创建构建或解析内容。
- 验证结果：核对 [FastAPI 上传文件](https://fastapi.tiangolo.com/tutorial/request-files/)、[Starlette 文件响应](https://www.starlette.io/responses/)、[SQLAlchemy 会话回滚](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) 及 [python-multipart 发行信息](https://pypi.org/project/python-multipart/)；在专用临时 PostgreSQL 容器中运行全量 `pytest -q`，`10 passed`，另有 1 个既有 TestClient 弃用警告。新增的 3 个集成测试检查三种格式、10 MiB 边界与超限、空文件、类型伪装、穿越文件名、同库重复及跨库独立、分页与原文件授权、成员被移除后的失效、数据库提交失败后暂存和最终文件均清理。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过；测试随机库均已删除。
- 遗留问题：PDF 只验证基本头尾结构，本步不解析，无法证明含可提取文本；扫描 PDF 将在解析步骤明确标记不支持。上传解析器可能在应用读取前将 multipart 文件暂存，实际部署还需在入口层配置请求体大小限制。文档保持 `uploaded` 且不可检索；删除、构建及模型调用未实现。数据库集成测试未提供 `TEST_POSTGRES_ADMIN_URL` 时会明确跳过。
- 下一步入口：等待新的编号任务；未来解析和构建应沿用文档私有存储键、权限守卫与 `active_build_id` 发布契约，先更新契约再修改实现。

## 第 9 步：Markdown/TXT 解析器

- 已完成：先更新 `ParsedSection` 架构契约，明确独立解析时 `document_id` 可空、`source_locator` 为原文行号、`section_index` 从 0 开始，并增加块类型。新增只读本地文件的 Markdown/TXT 解析器与 JSON 预览命令；支持 UTF-8 及带 BOM 的 UTF-8，保留 Markdown 标题层级、列表标记和代码围栏，去除段落外无意义空白；缺文件、错误编码、空正文、未闭合代码围栏、读取及内部解析异常有稳定错误码。未切块、写库或调用 Embedding，未新增依赖。
- 验证结果：核对 Python 官方 [UTF-8 BOM 编解码](https://docs.python.org/3.12/library/codecs.html)、[pathlib 文件读取](https://docs.python.org/3.12/library/pathlib.html)、[JSON 输出](https://docs.python.org/3.12/library/json.html) 文档。固定的 A 库 Markdown/TXT 模拟资料和专门构造的 Markdown 样例通过 12 项解析测试：标题路径、原文行号和段落顺序正确，`680 元`、`15 个自然日`、`NX-210-P`、HTTP 错误码与金额不丢失；BOM、代码不执行、CLI 输出和各类错误码均验证。项目全量 `pytest -q` 为 `15 passed, 7 skipped, 1 warning`：7 项数据库集成检查因未设置 `TEST_POSTGRES_ADMIN_URL` 而跳过，1 项为既有 TestClient 弃用警告。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过；首次 Ruff 检查因新文件长行失败，格式化后重跑通过。
- 遗留问题：这是面向演示语料的轻量 Markdown 解析，复杂的嵌套块、内联语法和 HTML 不做完整 CommonMark 语法分析；TXT 不推断标题。PDF 仍未解析；本步不产生检索结果或 RAG 效果结论。
- 下一步入口：等待新的编号任务；后续切块或索引构建可消费 `ParsedSection` 的原文行号与标题路径，必要的契约变化应先更新架构文档。

## 第 10 步：文本型 PDF 解析器

- 已完成：先更新架构契约，使 `ParsedSection` 的行号可空，并约定 PDF 的物理页码、逐页警告及 `complete/partial` 结果；后续构建不得把部分解析当作完整成功发布。新增 pypdf 解析器，逐页输出有文字的 `ParsedSection`，对空白页和无文字图片页分别记录警告；加密、损坏、整份无可提取文字及文件缺失返回稳定错误码。加入锁定的 `pypdf 6.19.0`、20605 字节的虚构四页固定 PDF、测试及使用说明。未接入 OCR、大型解析模型、切块或 Embedding。
- 验证结果：核对 [pypdf 文本提取与 OCR 限制](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)、[PdfReader 加密状态](https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html)、[PageObject 图片访问](https://pypdf.readthedocs.io/en/stable/modules/PageObject.html) 与 [PyPI 版本](https://pypi.org/project/pypdf/) 官方资料。`tests/test_pdf_parser.py` 的 6 项测试通过：固定 PDF 的中文、`680 元`、`2026-09-25`、`NX-210-P` 和第 4 页的 `15 个自然日` 保留；页码为 `[1, 4]` 而 section 序号为 `[0, 1]`；第 2、3 页分别为无文字及图片页警告，状态为 `partial`；纯文字 PDF 为 `complete`；加密、损坏和整份无文字均报明确错误。实际运行预览命令返回相同页码、警告和状态。全量 `pytest -q` 为 `21 passed, 7 skipped, 1 warning`；7 项数据库集成测试因未提供 `TEST_POSTGRES_ADMIN_URL` 而跳过，1 项为既有 TestClient 弃用警告。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过；首次 Ruff 检查的导入顺序和长行问题已修正后复查通过。
- 遗留问题：无文字图片页仅能标为疑似扫描页；含隐藏 OCR 文字层的扫描件可能被 pypdf 提取为文字，无法保证其正确性。暂不支持 OCR、复杂表格结构恢复和复杂双栏排版；本步未实现构建发布，因此 `partial` 阻止发布仍由未来构建服务落实。
- 下一步入口：等待新的编号任务；后续构建服务消费 `PdfParseResult` 时须检查 `status` 与逐页警告，只允许完整成功的构建按既有发布契约生效。

## 第 11 步：切块器

- 已完成：先更新架构契约，定义不含数据库 ID 的 `ChunkDraft`、逐段原文定位的 `ChunkSourceSpan`、可配置的字符级 `ChunkConfig` 与 `ChunkStats`；实现从 `ParsedSection` 到草稿的纯函数。按文档、标题路径、物理页分组，代码块独立，优先段落及中文句界，超长内容按字符拆分并仅在拆分时重叠。校验参数和跨文档混用，不生成空块或扩充短文本；无新增依赖或锁文件改动。README 增加预览命令及重叠取舍说明。未写库、生成向量或调用模型。
- 验证结果：核对 Python 官方 `dataclasses`、`hashlib.sha256` 和 Unicode 字符串文档。新增 18 项切块测试通过，涵盖默认值、短文本、标题与段落边界、中文句号、超长无标点段落、空文本、代码块及超长代码原文切片、PDF 跨页隔离与物理页定位、结果确定性、重复内容统计、非法参数和跨文档输入。全量 `pytest -q` 为 `39 passed, 7 skipped, 1 warning`；7 项数据库集成测试因未配置 `TEST_POSTGRES_ADMIN_URL` 跳过，警告为既有 TestClient 上游弃用提示。`ruff check .`、`ruff format --check .`、`uv lock --check` 和 `git diff --check` 均通过。实际运行 A 库报销资料预览得到 4 个草稿，输入/输出均 316 字符、无超限或完全重复块。
- 遗留问题：字符上限不是模型 token 上限；复杂排版和语义边界仍受上游解析质量影响。`short_chunks` 以块上限一半为阈值，样例按标题拆分后 4 块均被标记为短块，属于可观察统计而非自动质量结论。后续持久化必须保存或映射 `source_spans`，现有数据库 `chunks` 表还没有逐段字符范围字段；本步不证明检索召回效果。
- 下一步入口：等待新的编号任务；若将草稿入库，先更新契约及迁移以保留精确来源，并独立验证权限、构建发布和检索过滤。

## 第 12 步：模型适配层

- 已完成：先更新架构契约，并建立 `docs/model_config.md`，首版仅接 OpenAI。聊天模型和嵌入模型分开配置；默认分别为 `gpt-4.1-mini-2025-04-14` 与 `text-embedding-3-small`，嵌入维度配置为 1536。提供 `EmbeddingClient` / `ChatClient` 契约、OpenAI HTTP 实现、显式使用的可控 fake、usage/耗时/调用次数结果、连接与读取超时及最多 3 次有限重试。认证与参数错误、向量数量/索引/维度/非有限数值/零向量及结构化返回异常不重试；错误对象不含请求正文、密钥或供应商原始响应。`OPENAI_API_KEY` 只从环境变量加载，缺失时创建真实客户端明确失败。HTTPX 改为运行时依赖，加入 `jsonschema` 并更新 `uv.lock`。未接入完整 RAG、数据库向量列或 API 路由。
- 验证结果：查阅官方 [嵌入模型、维度和输入限制](https://developers.openai.com/api/docs/guides/embeddings)、[嵌入 API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)、[GPT-4.1 Mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)、[HTTPX 超时](https://www.python-httpx.org/advanced/timeouts/)及 [jsonschema 校验](https://python-jsonschema.readthedocs.io/en/stable/validate/)。16 项模型适配离线测试通过，覆盖 fake 确定性、分开配置、实际发送的超时值、向量与 JSON Schema 校验、错误重试策略、尝试次数和无密钥失败。全量 `pytest -q` 为 `55 passed, 7 skipped, 1 warning`；7 项数据库集成测试因未配置 `TEST_POSTGRES_ADMIN_URL` 跳过，警告为既有 TestClient 弃用提示。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 均通过。首次 NaN/Infinity 测试因 HTTPX 的 JSON 响应构造拒绝非有限值而失败；定位后改用原始响应字节，复跑通过。**真实接口未验证**：当前环境没有 `OPENAI_API_KEY`，没有调用收费 API；1536 是官方文档给出的默认维度，并非实测值。
- 遗留问题：尚无精确 tokenizer，本地不预判 8192 token 单条上限，超限由 API 返回 400 且不重试。OpenAI 严格结构化输出仅支持 JSON Schema 子集，调用方需提供兼容 schema；当前不做真实模型可用性、质量或用量实验。既有数据库尚无向量列，后续迁移与重建需把模型及维度配置标识一起管理。
- 下一步入口：等待新的编号任务；后续索引或问答流程显式注入真实或 fake 客户端，并保留技术错误、预算统计和构建发布边界。

## 第 13 步：单文档命令行入库

- 已完成：先更新架构契约，锁定本地已上传文档的单次入库流程。新增 Alembic 迁移，启用 pgvector 扩展，在 `chunks` 加入 `vector(1536)` 向量及原文 span，在 `document_builds` 保存 Embedding 提供方、模型、维度、配置版本、预期块数；历史行可为空，查询排除空向量。`ingest_document` 命令显式选择 `fake` 或 `openai`，输出构建 ID、状态、块数、模型配置 ID 与错误码。服务先短事务创建 processing 构建，关闭会话后校验私有原文件摘要、解析、切块并批量 Embedding；每批短事务写候选块；完整核对后锁定知识库，在短事务中发布 ready 构建与 `active_build_id`。失败构建不可检索，旧有效构建不变；同一 build ID 重复执行不追加块或重复调用模型。查询要求匹配完整模型配置 ID，同库不同配置（包括 fake/真实）不能同时发布。PDF 部分解析不发布。没有实现后台 worker、检索排序或问答。
- 验证结果：核对 [pgvector Python/SQLAlchemy 官方示例](https://github.com/pgvector/pgvector-python#sqlalchemy)、[pgvector 扩展](https://github.com/pgvector/pgvector)、[SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html) 和 [Alembic 操作](https://alembic.sqlalchemy.org/en/latest/ops.html)，在 `backend/uv.lock` 锁定 `pgvector 0.5.0`。用临时 PostgreSQL/pgvector 容器及随机命名、测试后删除的数据库运行全量测试，结果为 `69 passed, 1 warning`；覆盖完整发布、批次第二次调用失败时旧索引仍可见、重复构建无新增块、维度错误、同库配置隔离、PDF 部分解析失败、CLI 输出、迁移回退与重升，以及旧有授权/上传/解析/模型测试。fake 在调用 Embedding 时断言无数据库连接被占用。既有 TestClient 上游弃用提示仍在。首轮入库测试失败定位为无标题 TXT 的 Python `None` 被 JSONB 映射成 JSON `null`，触发 `ck_chunks_heading_path`；改为 SQL `NULL` 后集成测试通过。`ruff check .`、`ruff format --check .`、`uv lock --check` 和 `git diff --check` 均通过；随机测试库已删除，临时容器已停止。**真实 OpenAI 接口未验证**：环境没有 API 密钥，也未调用收费 API。
- 遗留问题：命令需要本地数据库操作权限，暂不提供 HTTP 入库接口或后台调度。处理中的构建若进程被强制终止会保持 `processing`，须人工诊断后再处理；本步不自动接管以免重复收费。部分失败构建可留有候选块，但始终不可检索。变更向量模型或维度需迁移并重建索引；现有 1536 维是官方文档配置，未由真实模型调用实测。
- 下一步入口：等待新的编号任务；后续检索必须传入与文档构建一致的 `model_config_id`，并继续限定知识库、有效构建与非空向量，真实模型验证单独记录。

## 第 14 步：向量检索服务与调试接口

- 已完成：先修订 `RetrievedChunk` 和检索接口契约，再实现 `POST /knowledge-bases/{kb_id}/search`。以 JWT 验证的用户 ID 调用 `require_kb_member`，嵌入前检查当前库有无兼容候选，模型调用期间不持有数据库事务，随后再次检查成员资格；SQL 同时限定当前库、未删除文档、ready 的 active build、完整模型配置 ID 和非空向量，用 pgvector cosine distance 做精确升序检索（无 ANN 索引），同距离按 chunk ID 排定稳定顺序。`top_k` 默认 5、只接受整数 1～20；返回定位、文档名、正文、物理页码/标题路径、distance、rank、request_id。显式 fake 配置可检查离线索引，默认真实 OpenAI 配置缺密钥时不回退 fake。空库返回空数组；模型故障返回统一错误响应。无新依赖或锁文件变更，未实现问答、混合检索或 Agent。
- 验证结果：核对 pgvector、pgvector-python、SQLAlchemy 和 OpenAI 官方文档。在临时 PostgreSQL/pgvector 容器中，人工构造查询向量 `[1,0,…]` 与候选 `[1,0,…]`、`[0.8,0.6,0,…]`、`[0,1,0,…]`，实测距离为 0、约 0.2、1，按预期排序。集成测试还检查 A/B 两库隔离、已删除/非当前/失败/配置不匹配构建排除、空库、`top_k` 边界、无令牌与非成员、成员移除、模型超时和零向量错误、显式 fake 与缺少真实密钥。全量 `pytest -q` 为 `73 passed, 1 skipped, 1 warning`；跳过项仅为真实语义检查，现有 TestClient 上游弃用警告仍在。显式运行 `RUN_REAL_RETRIEVAL=1` 的单项检查得到 `1 skipped`，原因是当前无 `OPENAI_API_KEY`；**真实语义检索未验证**，没有发送收费请求。`ruff check .`、`ruff format --check .`、`uv lock --check` 和 `git diff --check` 通过。首轮成员移除测试出现一次意外 `401`；单项复跑及全量复跑通过，该偶发现象与第 6 步记录相同，目前未定位，未作猜测性改动。
- 遗留问题：真实模型的语义排序与距离尚无实测，需按 README 的独立 opt-in 检查记录；fake 的 one-hot 排序只验证流程和 SQL。距离并非答案正确概率，也未设阈值或测召回效果。现有偶发 `401` 若再次出现，应保存认证失败分支及请求时间证据后定位。当前没有 ANN 索引，数据量增加后的延迟须实测后再决定是否加索引。
- 下一步入口：等待新的编号任务；后续问答可复用本服务返回的授权片段，引用来源仍须重新授权并验证删除与构建状态，不得把调试距离当作答案置信度。

## 第 15 步：固定流程 RAG 问答与受保护引用

- 已完成：更新架构流程和契约，新增 `POST /knowledge-bases/{kb_id}/answers` 及 `GET /knowledge-bases/{kb_id}/sources/{document_id}/{build_id}/{chunk_id}`。问答复用第 14 步成员授权、单库有效构建和模型配置过滤，按预算装入完整块并分配请求内引用编号。系统提示要求仅依据证据，资料中的指令作为不可信内容；Chat 只返回 `status`、`answer`、`citation_ids`。后端检查 schema、非空回答、引用集合和正文编号，成功答案缺引用、假引用、重复引用或模型编造元数据返回 `502`；无证据跳过 Chat 并返回资料不足，相关资料仍不足时允许不足或澄清。引用文件名、页码、标题、行号、原文及链接从真实来源补齐；来源接口校验完整 ID 链和当前权限。Chat 调用期间不持有数据库连接，返回前复核所有提供过的证据；删除或切换构建等变化返回 `409`，权限撤销返回 `404`。总预算默认 16384，以 UTF-8 字节保守计量消息与 schema，预留 1024 输出 token 和 1024 封装余量；真实 Chat 请求设置 `max_completion_tokens`。主要实现文件共 6 个，无新增依赖、迁移或锁文件变化，未进入 Agent 或后续任务。README 已提供 PowerShell 问答、来源访问和验收命令。
- 验证结果：已查阅 OpenAI 官方 [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 和 [输出 token 计量](https://developers.openai.com/api/docs/guides/token-counting)。使用可控 fake 与 HTTPX MockTransport，在专用临时 pgvector 容器和随机数据库中运行全量测试，最终 **86 passed, 1 skipped, 1 warning**；新增 13 项问答测试全部通过，覆盖正常引用、空检索不调用 Chat、假/缺失/重复引用、错误 JSON、超时、恶意资料的消息隔离、双来源冲突响应、资料不足和澄清、上下文整块预算、伪造元数据、来源 ID 链与跨库隔离、生成期间删除和撤销成员。测试内检查 Chat 调用时无数据库连接占用。1 项跳过为原有 opt-in 真实检索测试，警告为既有 TestClient 上游弃用提示；**真实 RAG 模型及效果未验证**，本步未发起收费 API 请求。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 均通过。首次 Ruff 发现提示词长行，已修正；上次全量回归被自动审批服务额度错误阻止、并未执行，本次继续后已补跑成功。临时数据库由测试删除，临时容器已停止并自动移除。
- 遗留问题：合法引用只证明来源属于本次提供的授权证据，不能自动证明答案在语义上受到证据支持；冲突识别与抗提示注入的真实效果不能由 fake 的固定输出证明，需独立人工评测。预算计量不是精确 tokenizer，切换模型须复核窗口和预留。来源只开放当前 active build，重建后旧引用返回不可用；引用编号 `c1` 等只在本次回答内有效。现有历史偶发 `401` 在本次全量测试未出现，未扩大范围修改认证。
- 下一步入口：等待新的编号任务；后续可复用固定问答服务和来源守卫，真实效果验证应使用已人工复核的数据并与 fake 流程测试分开记录。

## 第 16 步：向量检索基线评测脚本

- 已完成：先更新架构评测契约；新增 `app.evaluate` CLI 和 data/metrics/runtime 三个模块，仅在现有问答服务增加可选只读 trace，共 5 个主要实现文件。默认 dev 预检，显式 `--run-real`、全部所选样本人工复核、JWT 身份及库权限、语料哈希和有效真实索引配置均通过后才调用模型；test 显式选择并校验冻结摘要。复用同一次固定问答/检索，top_k 固定 5，不改检索 SQL、排序、过滤、上下文策略或提示词。实现基于稳定文档、完整证据原文及 source_spans 位置的 Hit@5、Evidence Recall@5、MRR@5，分别记录拒答、错误拒答、澄清、技术错误与未运行；保存引用合法性、检索/问答/模型耗时、已报告 token 及未知消耗。逐题 JSONL、JSON/Markdown 汇总、数据原文与哈希、代码/锁文件摘要、模型配置及构建切块参数落盘；错误 JSON/HTTP 响应体同样保留并脱敏已知 API 密钥，不记录请求头。索引快照漂移则该题指标无效。README 和 `docs/evaluation.md` 提供 PowerShell 命令、指标分母、复核与冻结说明；产物目录加入 Git 忽略。
- 验证结果：查阅 Python perf_counter/hashlib、HTTPX Event Hooks 和 SQLAlchemy Session 官方文档（链接见评测说明），沿用既有锁文件，没有新增依赖。13 项新增评测测试覆盖手算排名/召回、同文异位置和跨块未完整命中、无 gold 分母、技术失败与拒答分离、未知 token、draft 阻止真实调用、复核元数据与 test 冻结、假/缺失/重复引用、错误响应保存和脱敏、真实 PostgreSQL 中的单次 fake Embedding/Chat 观测、Chat 失败保留检索指标、索引变化检测。全量回归最终 **99 passed, 1 skipped, 1 warning**，使用本步专用 pgvector 容器与随机测试库；跳过项为原有 opt-in 真实模型检索测试，警告为既有 TestClient 上游弃用提示。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过；原 `data/eval/check_eval_set.py` 通过，题目及冻结文件未修改。首次集成测试复现向量值为 list、却调用 `.tolist()` 的序列化错误，已按数据库模型实际类型修复并回归通过；新增响应测试的缺失导入和格式检查错误也已修正。测试库由 fixture 删除，临时容器已停止并自动移除。
- 实际预检：在 `backend` 执行 `uv run --locked python -m app.evaluate --output ../artifacts/eval/step16-preflight`，退出码 **2**，状态 **not_run**。保留 15 条 dev（12 条可回答、3 条资料不足）的逐题未运行记录与汇总；阻塞码为 `UNREVIEWED_SAMPLES`、`REAL_RUN_NOT_REQUESTED`。效果、耗时和 token 均为 null，未编造零分或通过率；该本地产物不进入 Git。**真实 API 未运行，真实向量检索/RAG 效果基线尚未取得**，fake 测试数值不作为正式效果数字。
- 遗留问题：仓库全部 30 条样本仍为 draft，需项目负责人逐条人工复核后提供正式数据；本步没有替用户签署复核。真实运行还须准备与清单一致的真实模型索引、库映射及有效登录令牌。严格完整单元匹配可能低估跨块证据，当前章节定位面向已有 Markdown/TXT 模拟资料，不泛化为任意 PDF 标注。引用是否合法不能证明支持结论或事实正确，两项均留待独立人工复核。失败/重试 usage 可能不完整，耗时是服务调用观测值而非生产 HTTP SLA。小样本报告同时列分母，不推断总体效果。
- 下一步入口：等待新的编号任务；正式测量先按 `docs/evaluation.md` 完成人工复核、真实入库和环境配置，再显式运行 dev 并保存独立产物目录。不得依据 test 的错误反复调参，本步不实现后续检索策略。

## 第 17 步：BM25 单路检索

- 已完成：先修订 RetrievedChunk 契约及 BM25 预处理/授权/评测约定。锁定 `jieba 0.42.1`、`rank-bm25 0.2.2`（传递依赖 `numpy 2.5.3`），新增固定规则的 BM25Okapi 算法、授权服务和独立 `app.evaluate_bm25` CLI；连同块查询、统一结果及依赖配置，共 6 个主要文件。文档/查询同用 NFKC、casefold、ASCII 标识符保护和 jieba 精确分词 HMM=False；保留词频，无停用词或同义词扩展。先检查成员，再以 SQL 限定当前用户、当前库、未删除文档和 ready active build，只加载文本元数据；关闭会话后按请求分词建集合，返回前重新检查权限和集合变化。没有跨请求语料/分词/词频/结果缓存。无共同词项返回空，零/负分仍能作为真实匹配；按 BM25 分数降序及 chunk ID 升序排序。统一结果新增 bm25_score，BM25 distance 为 null，原向量查询 SQL 和问答策略未改。未做融合、重排、新 HTTP 路由或后续任务。
- 验证结果：查阅 jieba、rank_bm25 官方文档、BM25Okapi 官方实现及 PyPI 发行信息，链接和固定规则见 `docs/bm25.md`。新增 14 项测试通过，涵盖大小写/全角 ASCII、HTTP/E_AUTH_401/NX-210-P/NX-210-P2/小数、精确匹配与中文同义词局限、单文档负分和两文档零分、无共同词项、空/纯标点集合、稳定同分顺序、非法 top_k、权限先于建集合、A/B/未知库隔离、删除/非当前/失败构建排除、不依赖向量配置、成员移除立即失效、计算时无数据库连接、更新期间报错、draft 门禁、dev 完整诊断及临时复核夹具的正式指标分支。全量最终 **113 passed, 1 skipped, 1 warning**（跳过为原有 opt-in 真实语义检索，警告为既有 TestClient 上游弃用）。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过；新增代码/注释长行首次 lint 失败，缩短后复查通过。jieba 首次导入出现上游字符串转义 SyntaxWarning，导入和实际分词运行成功。未抑制警告或扩大依赖升级范围。
- 实际 dev 与成本：全量测试期间指定 `STEP17_BM25_OUTPUT=/home/grow/code/ragdesk/artifacts/eval/step17-bm25-dev-draft`，在专用临时 PostgreSQL 测试库中按原清单导入 8 份文档，经现有解析/切块/fake Embedding 入库后运行实际 BM25 算法；15 条 dev（A 12/B 3）全部完成，未调用真实 API。A 21 块/5306 正文字节/923 词项，B 5 块/1538 字节/289 词项。Linux x86_64、16 逻辑 CPU、串行 1 请求条件下，授权读库/分词含词典初始化/建 BM25/评分排序/复核平均耗时分别为 5.102/459.078/0.266/0.310/6.717 ms，总服务均值 **478.851 ms**；逐题 JSONL 与报告保存原始耗时、候选、分数及完整配置/构建/语料摘要。公开词典磁盘缓存已经前置测试暖启动，未声称整机冷启动或生产性能目标；每请求词典初始化是本轮主要成本。BM25 没有使用 fake 向量，fake 仅供测试数据准备。随机数据库均由测试清理，临时容器已停止并自动删除。
- 遗留问题：评测题仍全部 draft，已保存 BM25 单路原始结果与成本，但正式 Hit/Recall/MRR 为 null，不把候选诊断当已审核效果。原向量 `step16-preflight` 的 5 个产物哈希逐一比对未变，仍没有真实向量效果基线，不能报告两路质量优劣。源码、题目、词典和锁文件版本已记录；没有测并发容量或内存峰值，按请求初始化词典和遍历全集的成本随规模增长。常见词重叠不证明语义相关，中文同义改写和被拆开的型号仍有限制。当前只开放服务与独立评测入口，现有 RAG 保持向量单路。
- 下一步入口：等待新的编号任务；正式 BM25 效果测量需人工复核 dev，并对照相同语料/切块版本运行独立目录。执行命令和分数解释见 `docs/bm25.md`，本步不自动进入融合检索。

## 第 18 步：RRF 融合与三路对比

- 已完成：先更新 RRF/降级/配对评测架构契约。新增纯 RRF 函数、授权融合服务、配对评测模块和 `app.evaluate_rrf` CLI，扩展 RetrievedChunk，主要实现文件共5个。向量与BM25各请求最多20个候选，同块按chunk_id去重；同一路重复只以最小原始排名贡献一次，固定 `Σ1/(60+rank_i)`，默认输出5，保留vector_rank、bm25_rank、原distance和bm25_score。只用排名计算，内部Fraction精确比较，同分chunk_id升序。服务两路前后检查用户授权和完整当前语料，来源冲突或变化失败；无重排、新模型依赖或跨请求缓存。现有向量HTTP接口和固定问答未切换。
- 降级规则：FusionConfig默认严格；显式allow_degraded只允许模型暂时不可用/超时/网络故障、数据库池超时和明确临时SQLSTATE。权限、模型密钥/配置/维度、语料变化及未知异常均不降级；发生降级后仍重新授权和校验集合，双路临时失败返回RRF_ALL_ROUTES_FAILED。FusionResult始终携带trace，记录配置、各路原始排名/状态/安全错误码/耗时、degraded与最终状态；异常时保留调用方trace，不把技术失败变成资料不足。
- 验证结果：核对Python Fraction、SQLAlchemy异常、PostgreSQL SQLSTATE、Psycopg sqlstate和RRF官方说明，链接见 `docs/rrf.md`；无新增依赖，uv.lock未改。新增24项测试全部通过，包含手算公式、原始分数无关性、重复块不加倍、单路空/双路空、20候选与默认5、稳定并列、非法排名与来源冲突、两种方向的临时降级/严格失败、认证权限/维度/配置类失败不能降级、双路失败、返回前成员撤销、两路间正文改变、同次候选/单次Embedding、零或负提升保留、配对分母和索引漂移、draft/缺密钥阻止真实运行。`ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 通过，首次新增汇总字符串超长经缩短修复；原评测集检查通过，未修改题目或冻结摘要。
- 全量回归未通过：两次均为 **136 passed、1 failed、1 skipped、1 warning**。第一次 `tests/test_answers.py::test_relevant_chunks_can_still_be_insufficient[needs_clarification]` 意外401，request_id=e38babbafc4d4a5fb26c901760c4e783；同项复跑 **1 passed**。第二次 `tests/test_knowledge_bases.py::test_last_admin_cannot_be_removed_or_demoted` 意外401，request_id=2e1f19a30b9b4be9b882b8d9e8e6d6d5。检查认证签名/iat/exp/用户查询路径后，用临时包装器只在JWT异常时输出异常类型与时间字段，单次运行旧问答/知识库组 **15 passed、2 warnings**，没有捕获JWT异常；既有偶发401本次仍未定位。没有修改认证、放宽校验、跳过失败测试或继续随机重跑；保留此回归风险，不声称全量通过。真实语义检索测试仍为显式opt-in跳过，主要警告为既有TestClient弃用提示。
- 实际dev执行：隔离测试库按清单入库8份模拟资料，沿用600字符/80重叠与明确fake向量配置，执行真实BM25/RRF和fake向量检索。`artifacts/eval/step18-rrf-dev-fake/` 保存15条dev的三路原始候选、排名、trace和配置快照；15题全部完成、每题1次fake Embedding、0次真实API，模式fake_diagnostics。各单路前5与RRF来自同一次最多20候选，其他参数未调。正式指标、配对分母和提升差值不从fake计算：paired_n=0，指标/差值=null。实际执行 `uv run --locked python -m app.evaluate_rrf --run-real --output ../artifacts/eval/step18-rrf-real-preflight` 退出2，15题not_run，记录UNREVIEWED_SAMPLES与REAL_MODEL_KEY_REQUIRED。原第16/17步共10个产物文件的SHA-256逐一核对不变。
- 遗留问题：RRF实现与离线流程已验收；**真实三路效果对比未运行，无法判断有无提升**。全部30条题仍draft且环境无真实模型密钥，不能把未测量写成无提升或用fake代替真实语义效果。后续还需独立定位偶发401，当前全量回归状态不绿。降级结果不是完整双路RRF，配对汇总明确排除；小样本结论需看分母，引用支持度与事实正确性不在本步评测范围。
- 下一步入口：等待新的编号任务。真实dev对比需已人工复核文件、同版本真实有效索引和A/B映射及环境认证配置，按 `docs/rrf.md` 显式运行；不自动引入重排或调整参数追求提升。
- 清理结果：本步随机测试库由fixture删除，专用 `ragdesk-step18-pg` 容器已停止并自动移除；未操作已有用户数据、未push或部署。

## 第 19 步：可关闭重排器

- 已完成：先更新架构契约；用 6 个主要实现文件增加 `Reranker` 接口、Cohere `rerank-v3.5` HTTP 适配器、可控 `FakeReranker`、配置开关和独立配对评测。RRF 前 20 个已授权候选重排后取 5 个；完整检查索引范围/唯一性/数量/有限分数，同分保持 RRF 次序，保留各路及 RRF 原排名。外部调用前后重新检查授权和有效来源，无跨网络长事务。超时和临时不可用回退 RRF 并标记降级，认证、权限和非法响应不降级。配置入口默认关闭，无新增依赖。
- 官方依据：核对 Cohere v2 Rerank、模型/语言说明和 HTTPX 超时文档，链接见 `docs/rerank.md`。真实适配器目前仅完成接口实现及受控 HTTP 契约验证，不能称为真实服务已验证。
- 验证结果：新增定向测试在临时 PostgreSQL 中为 `35 passed, 1 skipped, 1 warning`；验证 20→5、候选伪造/重复/缺失/非有限值、稳定同分、关闭时不建客户端、空库、跨库隔离、调用期间撤权或修改来源以及超时仍不能绕过检查。真实 Cohere 检查因未配置跳过。完整回归 `172 passed, 2 skipped, 1 warning`；两项跳过为真实模型检查，警告为既有 FastAPI TestClient 弃用提示。本次未复现先前偶发 401，也未声称其原因已解决。首次新增代码静态检查发现导入顺序/长行问题，格式化整理后 `ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 均通过。
- 实验记录：`artifacts/eval/step19-rerank-dev-fake/` 完成 dev 15 题，15 次 fake Embedding 和 15 次 fake 重排；正式配对分母 0，质量指标/差值为 null，计费单位及金额未知。`step19-rerank-real-preflight/` 实际退出 2、状态 `not_run`，阻塞为 `UNREVIEWED_SAMPLES`、`REAL_MODEL_KEY_REQUIRED`、`RERANK_API_KEY_REQUIRED`；没有调用真实收费 API。保存数据/代码/索引配置、原始候选及响应；旧基线 20 份产物 SHA-256 校验均未改变。报告产物按既有规则留在 Git 忽略目录，方法和结论记入可提交的 `docs/rerank.md`。
- 遗留问题：真实接口、中文检索质量、真实延迟和调用费用尚未验证；15 道 dev 仍为 draft。HTTPX 阶段超时不是严格总墙钟时限，超长候选可能由提供商截断；已在配置说明列明。缺少真实收益证据，**默认关闭**，不报告效果提升。没有把重排分数解释为答案置信度，未切换现有 HTTP 问答策略。
- 下一步入口：等待下一项编号任务；本步真实验证可按 `docs/rerank.md` 先运行单次 Cohere 检查，再以人工复核 dev 和真实索引执行配对评测，评估质量差值、样本量、增量延迟与 search_units 后再决定是否开启。

## 第 20A 步：数据库入库任务与单 worker 正常流程

- 已完成：先更新架构契约，在 6 个主要实现文件内新增 `ingestion_jobs` 模型和 Alembic `0004_ingestion_jobs` 迁移、任务服务、独立 worker，调整上传服务和文档路由。任务保存文档/请求用户外键、同文档构建外键、状态、尝试次数、安全错误摘要、配置快照和时间。上传把文档与 queued 任务同事务提交，返回 `202 + job_id + status_url`；显式入库入口也返回 202。重复请求复用活动任务，重复上传复用最近终态任务；文档行锁及数据库部分唯一索引确保同文档最多一个活动任务。API 不解析文件、不调用模型。
- 执行与权限：单 worker 按顺序用短事务领取，提交 running 后关闭会话，再调用未改动的 `ingest_document` 服务，成功/失败后短事务保存任务终态及实际 build。既有 CLI 保留；运行中的 build_id 可以为空。任务状态查询沿用架构的管理员权限，复用 require_kb_admin 并校验库/文档/任务完整链；无权限与不存在统一 404、普通成员 403。正常失败不破坏旧 active build；错误只存机器码，不存供应商原文。任务保存服务端模型配置，缺真实密钥明确失败，不自动使用 fake。
- 验证结果：专用 `ragdesk-step20a-pg` PostgreSQL/pgvector 容器、随机独立测试库中运行定向组 **19 passed, 1 warning**；全量回归 **180 passed, 2 skipped, 1 warning**。新增 8 项任务测试覆盖：上传只排队不调用入库、202 和状态推进、单独 Python 子进程 worker 成功/空队列、8 次并发请求只新建一个活动任务、queued/running 去重、任务跨库/伪造文档/未知 ID/普通成员/撤权隔离、模型调用期间连接池无占用且另一事务可 NOWAIT 取得任务/文档/构建锁、失败重建保留有效索引、解析失败后能处理下个任务、缺密钥及未知异常安全失败、入队异常回滚文档/任务并清理文件、外键/唯一/检查约束及迁移回退再升级。全量同时验证既有 CLI 调试入口、原有迁移链与 Alembic 模型无差异；仅在测试库回退。两项跳过是未配置的真实模型检查，警告为既有 FastAPI TestClient 弃用提示。历史偶发 401 未在本轮出现，未扩展修改认证。
- 性能观测：定向测试用 TestClient 在进程内上传 20 字节演示 TXT，单次请求 **39.819 ms，n=1**；worker 未启动且入库函数被禁止调用时已返回 queued，证明上传不等待 Embedding。此数值仅是本机单次观测，不代表真实网络延迟、吞吐量或 SLA。全部入库验收使用 fake，没有收费 API 调用或新增效果结论。
- 工程检查：核对 SQLAlchemy 事务、PostgreSQL 部分唯一索引及行锁、FastAPI 状态码官方文档（见 `docs/ingestion_jobs.md`），未新增依赖或修改锁版本。首次静态检查发现两个长字符串和一个未使用导入，调整后 `ruff check .`、`ruff format --check .`（83 文件）、`uv lock --check`、`git diff --check` 通过。README 和环境示例已更新，提供 PowerShell 迁移、上传、任务查询及单 worker 命令。
- 遗留问题：**只支持单 worker 正常流程**，没有心跳/租约/重领/自动重试/崩溃恢复，也没有分布式容错承诺。构建发布与任务终态分开提交，强制退出可能留下 running、阻止同文档新任务，即使文档已经 ready；build_id 在任务完成时才关联，排队或运行时可空。文件与数据库间的崩溃清理窗口仍存在。后台执行基于已接受的管理员授权，不因提交者后续撤权自动取消，查询仍重新鉴权。管理员需停止 worker 后独立使用原 CLI，CLI 不同步已有队列任务状态。
- 下一步入口：等待第 20B 步；未来针对 running 遗留、发布/终态之间的崩溃窗口制定恢复与重试契约并先定义故障注入测试。本步不提前实现恢复。

## 第 20B 步：任务租约与故障恢复

- 已完成：先更新恢复契约，在 6 个主要实现文件内加入 0005_job_leases 迁移、lease_expires_at/heartbeat_at/run_token/max_attempts、过期重领、独立心跳及执行令牌校验。默认租约 60 秒、心跳 10 秒，CLI 可调；租约判断读取 PostgreSQL clock_timestamp，截止时刻即失效，过期或旧令牌不能续租。每次领取新 run_token 并创建独立构建；构建创建时即关联任务，旧 processing 构建废弃，旧 ready active 构建保留。所有构建写入/失败更新参与任务令牌检查；发布事务同时检查 running、当前 token、未过期租约、未删除文档和完整块，原子提交 build ready、active_build_id 与 job succeeded，消除 20A 新执行的发布/任务成功分离窗口。任务查询增加租约、心跳和预算字段，不暴露 run_token。
- 重试规则：任务默认最多 3 次（数据库预算范围 1～3），崩溃计入尝试。明确的 MODEL_TIMEOUT/MODEL_NETWORK_ERROR/MODEL_UNAVAILABLE 在剩余预算内重新 queued，永久配置/认证/解析/维度及未知错误终结 failed；第三次崩溃过期后下一次轮询记录 RETRY_EXHAUSTED，不进行第四次。正常临时错误耗尽保留原错误码。仍按单 worker 部署；迁移前必须停止无令牌检查的旧 20A worker。迁移保留已有任务，可定位并废弃旧 job.id 下的 processing 构建，重领前已发布的有效构建不删除。原 CLI 与既有发布约束继续有效。
- 故障注入：新增 16 项测试，以 UTC 可控时钟和 Event 同步，覆盖领取后退出、Embedding 返回后退出、发布前退出、发布事务中退出、旧 worker 延迟成功/报错、心跳续租与过期拒绝、三次崩溃耗尽、连续临时错误最多三次、三类永久错误一次终结、发布前删除、未被重领但已过期不能发布、20A 遗留任务迁移恢复。发布事务内异常会同时回滚任务成功/构建 ready/active 指针；恢复后可检索集合只有新构建，重复执行同一 claim 不新增有效块。故障注入实际观察 fake 模型调用两次，明确外部请求可能重复，不能宣称端到端 exactly-once。
- 实际验证：首次 Docker 不可用、31 项数据库测试全部跳过，未计为通过；通过本机 Docker Desktop 启动命令恢复引擎后，在专用 ragdesk-step20b-pg 和随机测试库执行。首轮定向测试为 **30 passed, 1 failed, 1 warning**，唯一失败发生在 test_deleted_document_cannot_publish 的上传准备阶段，意外 401，request_id=417efc4a56e64590af9e73aa613797e7，尚未进入恢复逻辑。检查现有认证分支后，用临时 pytest 包装器仅在失败时记录异常类型、iat/exp/当前时间和分支行号（不记录令牌、密钥、用户内容），该项复跑 **1 passed**，未再出现异常认证。随后的全量回归 **196 passed, 2 skipped, 1 warning**，新增 16 项全部通过；两项跳过为未配置的真实模型检查，警告为既有 TestClient 弃用提示。历史偶发 401 原因仍未定位；未改认证或放宽校验。临时诊断脚本已移除，首次失败/单项诊断/全量输出保存于 Git 忽略的 artifacts/validation/step20b/。
- 工程检查：核对 PostgreSQL 时间函数/行锁、SQLAlchemy 事务和 Python Thread/Event 官方文档（链接见任务说明）；无新增依赖，沿用锁定版本。ruff check、ruff format --check（85 文件）、uv lock --check、git diff --check 通过；Alembic 模型差异检查及升级/回退在独立测试库通过。README、架构及 docs/ingestion_jobs.md 更新了迁移、租约参数、状态、故障注入和一致性边界。没有真实模型或收费 API 调用，不产生真实 RAG 效果结论。
- 遗留问题：数据库事务不能覆盖供应商处理与计费，崩溃或网络结果丢失后外部调用仍可能重复；失败候选保留供排查，未做垃圾清理。模型主调用不持有数据库事务，心跳使用独立短事务；无法强制撤回已发出的模型请求。恢复需要数据库可用且 worker 继续轮询，不宣称完整分布式任务平台或 exactly-once。旧版二进制必须停止后升级；文件孤儿清理及 CLI 与 worker 并行运行不在本步范围。历史偶发 401 保留为独立未定位问题。
- 下一步入口：等待新的编号任务；继续使用本步的执行令牌与原子发布契约，不自动扩展任务平台或其他功能。测试数据库由 fixture 删除，专用容器在本步结束时停止并自动移除。

## 第 21 步：文档删除与同一原文件索引重建

- 已完成：先更新架构契约，在 6 个主要实现文件内新增管理员 DELETE 与 `/rebuild` 入口，复用已有任务/入库/授权服务。删除在一个短事务内设置 deleted_at、清空 active_build_id、取消 queued/running 任务（failed + DOCUMENT_DELETED）、撤销 token/租约并废弃 processing 构建。入队和删除共享文档事务咨询锁，删除按任务→文档→构建顺序加行锁；延迟 worker 无法重新发布。原向量/BM25 的有效构建过滤继续生效，删除后原文件、引用及任务查询也不可见。
- 重建与引用：同一原文件新建任务/构建，重复活动请求复用任务；旧有效索引在完成前和失败后继续服务，完整成功后原子切换。重新校验 SHA-256，未实现上传覆盖。排队、CLI/worker 构建开始及发布均检查旧有效模型身份兼容；模型变化为 INCOMPATIBLE_REBUILD_CONFIG，非 1536 维服务配置为 INVALID_EMBEDDING_CONFIG，需要另行迁移，不混用。真实旧 ready 构建引用对授权成员为 410 SOURCE_EXPIRED，未知/伪造链、删除或无权限仍 404，不回传旧正文或映射新文本。
- 文件边界：数据库提交后只清理受控目录下的服务端对象键，目录句柄及 O_NOFOLLOW 防止跟随符号链接；数据库提交失败不删文件。清理结果明确区分 removed/missing/blocked/pending；同库管理员重复 DELETE 幂等并可重试清理。当前 Windows 原生缺少所用目录句柄能力时保留私有文件并返回 pending；本次在 Linux/WSL 实测，能力缺失分支通过受控测试验证，未声称 Windows 实机物理清理已验证。
- 实际验证：首次新增测试 **9 passed, 1 failed, 1 warning**；失败定位为测试直接修改 frozen Settings，尚未进入维度拒绝逻辑。改用配置副本后，删除/任务恢复/入库/引用定向回归 **56 passed, 1 warning**。工具一度因自动审批用量限制中断，但定向回归实际已结束并保存结果；恢复后继续完成。最终在专用 ragdesk-step21-pg、随机独立数据库全量运行 **208 passed, 2 skipped, 1 warning（75.87 秒）**；新增 12 项全部通过，两项跳过为未配置的真实模型检查，警告为既有 TestClient 弃用提示。没有真实 API 或收费调用，结果不代表真实 RAG 效果。历史偶发 401 本次未复现，未修改认证或宣称问题已解决。
- 验收覆盖：成员/跨库权限、删除后向量与 BM25 及来源/原文件/列表不可见、取消尚未领取任务、提交失败回滚且保留文件、Embedding 后与发布前删除不复活、删除与入队两种先后顺序无遗漏活动任务、重建成功前旧索引可查及成功后新索引独占、旧引用 410 与伪造链 404、失败重建和原文件被改保留旧索引、模型/维度拒绝、同名新文件不覆盖、路径穿越/符号链接/不支持平台清理、清理失败可见且可重试。竞争用 Event 同步，不等待长租约或随机 sleep。
- 工程检查：首次 ruff 发现长行/导入间距，格式化整理后最终 `ruff check .`、`ruff format --check .`（86 文件）、`uv lock --check`、`git diff --check` 全通过。无新增依赖或数据库迁移，沿用锁文件；全量包括既有迁移检查及独立测试库回退。核对 PostgreSQL 17 咨询锁和 Python 3.12 dir_fd/unlink 官方文档，链接与 PowerShell 操作见 docs/document_lifecycle.md；README、架构及任务状态说明同步更新。原始验证输出保留于 Git 忽略的 artifacts/validation/step21/。
- 遗留问题：数据库与文件系统不共享事务，提交后进程退出/文件系统失败可能留下私有原文件；没有自动垃圾回收，历史块保留但不进入检索。模型/维度变更需单独设计全库迁移，不在单文档重建中直接切换；外部模型调用无法撤回，仍可能重复计费，不承诺 exactly-once。依旧只部署单 worker，不扩展文件版本管理。
- 下一步入口：等待新的编号任务。继续遵循本步的软删除、原子发布、模型兼容和旧引用失效契约；不自动实现后续功能。本步测试库由 fixture 删除，专用测试容器随后停止并自动移除；未操作已有用户数据、未 push 或部署。

## 第 22 步：请求级 trace

- 已完成：先更新契约，在 6 个主要实现文件内增加 answer_traces 模型及 Alembic 0006 迁移、精简 trace 服务、问答/模型包装、HTTP 生命周期和受保护查看接口。每次问答使用服务器 request_id，成功、拒答、澄清、超时、无权限、参数错误和未处理异常均尝试保存。保留用户/知识库 ID 快照、策略、候选与引用 ID、阶段/模型/整体耗时、调用次数、usage 和安全错误码。事件以 event_id/parent_event_id/kind 表示阶段、模型和预留工具事件；本步不实现 Agent 或额外监控平台。
- 隐私与权限：字段白名单不保存问题、答案、提示词、文档正文/文件名、向量、密钥、请求头或供应商原文。既有显式离线评测的完整原始输出与请求 trace 分离。GET /knowledge-bases/{kb_id}/traces/{request_id} 每次查当前成员资格，只允许请求本人或该库管理员；其他成员、跨库、未知或撤权统一 404。未认证身份记 null，用户身份不取自请求体。
- 用量与成本：原向量策略保持不变，重排明确 not_run；支持重排计量包装并以 fake 验证，未自动接入问答。真实适配器有完整 usage 才标 reported，重试只收到末次 usage 时保留 partial 明细，缺少计数/用量为 null；fake 记 simulated，不把零 token 当真实免费。环境 TRACE_PRICES 要求精确模型身份、币种、有效日期及有限非负单价，使用 Decimal 按 token/search_units 估算并保存价格及日期快照；缺价、过期、未知使用量或混合币种均不生成完整费用。没有配置任何默认真实供应商价格。
- 定向验证：首轮 trace+问答 **32 passed, 1 warning**；补充未知调用数、未处理异常和多币种边界后 trace 单独 **22 passed, 1 warning**，随后加入非法 JSON 价格配置验证。检查跨用户/库/撤权、内容白名单、失败留痕、写库失败仍保持原响应并标 unavailable、计数/价格语义、事件父子关系及独立测试库迁移回退再升级。无真实模型或收费 API 调用。
- 实际示例：TestClient、JWT、临时 PostgreSQL 与显式 fake Embedding/Chat 实际执行一次问答，并通过受保护接口读回已保存 trace。request_id=4aaffc50b2ae455f9966f7d9d36d8d1b，总耗时 57.889 ms；请求准备 33.887 ms，检索含 Embedding 15.154 ms，Embedding 0.035 ms，检索非模型 15.118 ms，Chat 模型 1.521 ms，所有模型 1.557 ms，来源复核 5.568 ms。检索非模型部分比模型更长，但总耗时最大段是请求准备，不能称模型瓶颈；n=1 fake 样本不外推真实 API 性能。原始脱敏 JSON 及验证日志保存于忽略目录 artifacts/validation/step22/；分析见 docs/traces.md。
- 工程检查：无新增依赖，沿用 uv.lock；核对 SQLAlchemy 事务、Python perf_counter 和 Decimal 官方文档，链接见 docs/traces.md。README、架构、环境示例和 trace 配置说明已同步；首次静态整理后 ruff check、ruff format --check（89 文件）、uv lock --check、git diff --check 通过。
- 遗留边界：trace 在响应生成后、独立短事务保存；total_ms 不包含 trace 写库和网络发送。写库失败不会改变问答结果，X-Trace-Status 明确 unavailable；强制退出前未保存的请求可能丢失。尚未实现自动保留期清理，数据会随请求累积。未运行真实模型，不给出真实调用费用或延迟结论；历史偶发认证 401 仍为独立未定位问题。
- 全量验证与清理：在专用 ragdesk-step22-pg 和随机临时库全量运行 **231 passed, 2 skipped, 1 warning（84.42 秒）**，新增 trace 23 项全通过；两项跳过为未配置的真实模型验证，警告为既有 TestClient 弃用提示。本次未复现历史偶发 401，未修改认证。迁移仅在临时库升级/回退，已有用户数据未操作。随机库由 fixture 清理，专用容器在本步结束时停止并自动移除；未 push 或部署。
- 下一步入口：等待新的编号任务；已有环境先显式升级到 0006 并重启 API，按 docs/traces.md 查看本人的请求或管理员授权范围内的 trace。不自动引入 Agent、监控平台或调整检索策略。

## 第 23 步：只读 Agent 工具（2026-09-28）

- 已完成：新增 agent/contracts.py、agent/tools.py、agent/__init__.py 和 services/tool_sources.py，共 4 个主要实现文件。仅封装 search_knowledge/read_chunks，复用向量检索、当前来源查询和 require_kb_member；没有 Agent 循环、新 HTTP、写入工具或策略调整。先补充架构契约及权限/错误用例，再实现工具。
- 安全与有效性：后端 frozen RunContext 注入已验证用户与单库；严格参数 schema 排除身份字段。所有调用重新授权，read 只接受同一运行最近一次检索实际输出且仍为 active ready 的块，复核来源链和正文哈希。删除、重建、撤权、伪造或跨库 ID 均不能读出正文；批量验证失败不返回部分内容。
- 返回与预算：明确 success/no_results/permission_denied/invalid_arguments/technical_failure/budget_exceeded；默认预览 240、读取 1600、紧凑序列化响应 8000、累计正文 12000 字符。限制文件名/标题，记录截断及省略数量；预算和允许集合每请求独立。可选接入现有 tool/model trace 事件，不保存工具参数或正文，不在工具内持久化 trace。
- 定向验证：真实专用 PostgreSQL+pgvector、随机临时库与 fake Embedding，**21 passed, 1 warning（10.32 秒）**。覆盖正常检索/读取、跨库/伪造/未检索 ID、成员移除（含 Embedding 期间）、超长 query、越界/非法 top_k、删除和重建失效、正文变化、最新检索集合、失败分类、序列化与累计预算、trace 元数据。未调用真实 API，不宣称 Agent 或语义效果提升。
- 修复记录：初次定向测试 20 passed / 1 failed，失败为重建测试夹具缺少位置字段触发 ck_chunks_locator；上一轮修复因自动审批用量限制未执行。本次首次重试因系统无 python 别名仍未写入，后改用项目虚拟环境补齐 page_number=1，通过全部 21 项。同步修正一条超长源码行；预算计算先检查完整候选，避免移除截断标记时 JSON 长度变化导致不必要截断。
- 全量回归：**251 passed, 1 failed, 2 skipped, 1 warning（100.60 秒）**。失败为既有 test_delete_hides_every_read_and_allows_new_upload 的成员删除请求预期 403 却收到 401；第 23 步全部通过。历史进度已有同类偶发认证问题，本次检查到失败发生在 HTTP 认证路径，此路径未接入新工具；根因尚未定位，不修改认证、不把全量报告为通过。两项跳过为真实模型验证，警告为既有 TestClient 弃用提示。原始输出保存于忽略目录 artifacts/validation/step23/。
- 工程检查：ruff check 通过；ruff format --check 共 94 文件通过；uv lock --check 解析 50 个包通过；两个工具的参数与返回 JSON Schema 均通过 Draft202012Validator.check_schema；git diff --check 通过。无新增依赖或迁移，沿用 uv.lock；官方依据及可复制 PowerShell 验收命令见 docs/agent_tools.md，README 已同步。
- 遗留边界：正文按字符限额而非 token，固定响应封装仍需未来调用次数/总上下文预算；frozen 上下文不是任意宿主 Python 代码的沙箱。只有本次检索返回的 ID 可读，来源过期需重新搜索；已返回的文本不能追回。没有真实模型验证，也没有新增 Agent 编排。
- 下一步入口：等待新的编号任务。第 23 步直接工具测试已通过；后续若启动编排，应复用同一请求的工具实例、明确总调用预算并保存 trace，同时跟踪既有偶发认证 401。不会自动开始下一步。
- 失败用例复查：不修改任何认证/文档代码，单独重跑上述用例得到 **1 passed, 1 warning（2.11 秒）**（lifecycle-recheck.txt）。单次重跑通过不能证明偶发 401 已修复，保留全量失败结论和遗留问题。
- 清理：fixture 已清理随机测试库；仅停止本步创建的 ragdesk-step23-pg 专用容器（--rm 自动移除），未操作已有用户数据库，未提交、push 或部署。

## 第 24A 步：单次工具决策 LangGraph（2026-09-28）

- 已完成：先定义架构契约和安全/终止测试，再新增 agent/decision.py、agent/graph.py，修改 agent/tools.py 和 services/answers.py（4 个 Python 实现文件，另更新依赖声明/锁文件）。图是 START→decide→可选 execute→finalize→END，无回边；不新增 HTTP 或自动循环。run_once 是可信后端入口，状态含问题、证据、工具/模型调用计数、deadline、final_result，身份来自 frozen runtime context。
- 决策：OpenAIToolDecisionClient 复用现有 OpenAI HTTP 适配器的超时、有限重试和配置，原生 Chat Completions function tools 使用 auto、parallel_tool_calls=false、strict。后端双重拒绝未知工具、多调用、错误结构/JSON、重复 JSON 键、非法参数与身份字段。模型不调用时丢弃其自然语言；FakeDecisionClient 支持固定输出和故障注入，不能绕过图校验。
- 证据与权限：工具内保存最近输出快照，图不接受模型或调用方注入 evidence。决策前、工具调用及最终生成前后重新授权并复核 active ready 来源与原始正文哈希。首次选择 read 无登记候选时拒绝；成功 read 测试先由可信后端显式预检索同一工具实例，不将这次准备伪装为图自主行为。最终生成提取复用第 15 步上下文、引用校验和 Citation 拼装；引用只返回工具实际提供的片段，无证据不调用 Chat。
- 终止与 trace：一工具实例只允许一次图调用；状态 tool_call_count 为 0/1，model_call_count 计本图模型方法调用（包含 Embedding，网络重试次数另记 trace）。默认 deadline 60 秒，检查外部调用边界并丢弃迟到结果，同步网络调用不能被强制取消。权限/技术失败返回 error 且 final_result=null，不冒充拒答，清除成功工具正文和最终引用。trace 仅记录合法工具名、参数长度/数量摘要、结果数及状态，不含思维链/正文；图显式关闭 LangSmith tracing，不使用 checkpointer 或监控服务。
- 定向测试：首次 **70 passed（30.94 秒）**，覆盖新增图分支、原 Agent 工具与固定 RAG 回归，无测试失败。静态检查先修复一处 import 排序与测试 lambda 赋值。复核后补充既有证据直接终止、失效来源在决策前阻止、相关片段仍不足、最终 Chat 超时四项；并在模型发送前复查 deadline，权限/超时失败清除状态中的成功工具正文。
- 最终全量验证：专用 ragdesk-step24a-pg、随机临时数据库，**292 passed, 2 skipped（114.50 秒）**；本步 test_agent_graph.py 共 40 项全部通过。覆盖模型实际选择搜索参数、已有候选 read、无工具/空库、非法工具与参数/结构/多调用、撤权（决策和 Chat 期间）、删除、假引用、截断及恶意资料提示边界、可控时钟超时、调用次数与 trace 脱敏、原生 HTTP 请求/响应契约及不重试坏输出。两项跳过为真实模型验证；本次未复现既有偶发 401，不宣称该历史问题已修复。无真实模型或收费请求，不能当成自主检索效果实验。
- 工程验证：ruff check、ruff format --check（97 文件）、uv lock --check（77 包）、git diff --check 通过；两个提供商工具参数 schema 均通过 JSON Schema 结构检查。实际编译图的 Mermaid 已检查，无回边。新增 LangGraph 1.2.12，锁定完整传递依赖；原包版本未更新，新传递依赖 httpx2 使 Starlette 不再发出此前 httpx 弃用提示。依据已查询的 LangGraph Graph API/Quickstart 与 OpenAI Function calling/GPT-4.1 mini 官方文档，链接及配置说明见 docs/agent_graph.md。
- 文档与产物：README、architecture、单次图说明同步；PowerShell 可复制命令和后端工厂注入示例见 docs/agent_graph.md。定向/全量原始结果保留于忽略目录 artifacts/validation/step24a/。没有记录私有思维链，完整图 state 含正文，仅供可信后端使用，不应直接公开或持久化。
- 遗留边界：真实 tool calling 接口未验证；离线 HTTP 与 fake 只验证协议、分支、安全控制，不能证明模型工具选择质量或所有提示注入防护。引用有效不等于结论正确，仍需人工评估。deadline 不是强制网络取消，截断片段可能缺少例外条款；未来宿主需负责 trace 保存和错误响应映射，本步没有公共 Agent API。
- 下一步入口：等待新的编号任务。本步单次决策与工具测试通过，不自动增加循环、重试决策、跨请求记忆或调整检索策略。已有环境先 uv sync --locked；继续追踪历史偶发认证 401，不扩大本步范围。
- 清理：测试 fixture 清理随机库，专用 ragdesk-step24a-pg 已停止并通过 --rm 自动移除；未操作既有用户数据库，未 commit、push 或部署。

## 第 24B 步：有预算的再次检索

- 已完成：先更新架构契约与验收用例，在 4 个主要实现文件内新增 agent/loop.py、llm/budget.py，修改 agent/decision.py、llm/openai.py。run_agent 使用 LangGraph 受限回边，读取中间结果后可改写查询、补读、澄清或结束；保留 24A 单次入口，不新增 HTTP、跨请求记忆或检索策略。身份仍为后端 frozen RunContext，每轮独立工具实例和私有 trace。
- 硬限制：工具最多 3 次；共享预算在每次模型请求尝试前扣减，最多 6 次（包含决策、Embedding、最终 Chat 及 HTTP 重试），非最终调用预留最后 1 次额度。整轮默认最多 60 秒，可缩小不可放大。累计工具结果紧凑 JSON 最多 24000 字符，正文仍最多 12000 字符；每次决策输入有独立上限。规范化 query 与 read ID 列表识别重复调用，第二次不执行。termination_reason 区分预算、重复、澄清、正常结束与技术错误。
- 网络执行：预算路径使用 AsyncClient、transport retries=0、禁止自动重定向；connect/read/write/pool 超时均不大于剩余时间，另以 asyncio.timeout 限制整次请求。显式重试逐次计数，退避也受 deadline 约束；未知客户端或自定义同步传输不能绕过限制。fake 仅模拟一次受控请求，真实失败不自动回退 fake。监督器到期停止等待、取消预算并禁止迟到结果发布；暂时不可中断的只读 DB/fake 操作可能稍后退出，不能撤回供应商已经处理的请求或费用。
- 回答与权限：最新检索集合替代旧集合，历史仅记录查询、状态、ID 与片段变化，不在后续提示保留失效旧正文。最终生成前后和返回前复核当前成员、有效构建、原正文哈希及引用。无证据不生成事实，未恢复技术故障不伪装为无结果。request_clarification 仅允许缺失条件枚举，由后端生成澄清句，不采用模型自述企业规则，不计为新的资料工具。
- 查询与 trace：按本步明确要求，此 Agent 路径记录实际 query（每条不超过 4000 字符、最多 3 次），对此前默认不记 query 的规则作局部变更。记录 added/removed/changed ID、结果数、输出长度；不记录资料正文、密钥配置、完整提示词或思维链。工具开始先记 running，结束再补结果；监督器超时快照标 trace_incomplete=true，未完成结果/usage/费用保持未知。trace 不共享仍由后台修改的对象。
- 实际验证：首轮循环+单次图+模型适配器定向 **77 passed（21.67 秒）**。补充未知适配器、三个工具后最终生成、交换 read ID 顺序去重和未恢复超时后，全量 **317 passed, 2 skipped（122.09 秒）**；两项跳过为真实模型验证。本次未复现历史偶发认证 401，不宣称该问题已修复。随后复核发现“执行中超时尚未记 query”的记录缺口，改为执行前登记，并增加事件阻塞用例；最新循环测试 **26 passed（10.97 秒）**。最后记录修正仅做该组定向回归，未将此前全量结果冒充修正后的全量执行。没有失败测试或真实收费 API 调用。
- 验收覆盖：首次工具超时后模型读取错误并改写成功；搜索→补读与证据变化；重复 query/read 参数；三工具上限、六请求上限含重试与最终生成、第七次不发出；上下文超限不发送未获准正文；澄清、始终无证据；决策/Embedding/Chat 中撤权及删除；可控时钟过期、短 Event 阻塞时监督器返回、异步 HTTP 取消、随剩余时间缩小的各阶段超时；非法上限与未知客户端拒绝。真实网络协议使用 MockTransport，未运行真实模型。
- 实际演示：单独执行失败改写用例 **1 passed（1.86 秒）**。request_id=11675c11762a425bbbd43788013bdc67，query“报销规则”返回 MODEL_TIMEOUT，模型随后改查“审批期限 680 CNY”，新增 1 条证据，最终 answered；共 2 次工具、6 次模型请求、817 字符累计工具结果。该 n=1 fake 记录验证流程与预算，不能报告真实效果提升。原始输出见忽略目录 artifacts/validation/step24b/demo.txt，定向/全量日志同目录。
- 工程检查：完成时 ruff check、ruff format --check（100 文件）、uv lock --check（77 包）、git diff --check 通过；检查实际编译图的受限回边及提供商 schema。无新增依赖，沿用锁文件；已查询 HTTPX 超时/AsyncClient、Python asyncio 超时及 LangGraph Graph API 官方文档，链接见 docs/agent_loop.md。README 与架构已同步，说明含后端入口、PowerShell 命令和实际 fake 轨迹。
- 中断与恢复：此前收尾写入与清理命令被自动审批用量限制拒绝，未执行。本次继续核对时首次自动审核超时，按提示重试一次成功，确认日志与代码仍在，补写本节及演示记录。当前 docker ps 未发现运行中的 ragdesk-step24b-pg，无需再停止；测试 fixture 已清理随机库。未操作既有用户数据库，未 commit、push 或部署。
- 遗留边界：真实 tool calling/效果未验证；引用合法不等于事实正确。完整 state 含正文仅供可信后端使用，公共响应应仅返回结果、结束原因和安全错误；未来 HTTP 宿主负责私有 trace 持久化及授权查询。监督器不强制杀死任意 Python/数据库操作，但不发布迟到答案且阻止后续模型请求，不承诺远端不计费。查询记录自身属于私有内容，不能公开。
- 下一步入口：等待新的编号任务，不自动扩展 Agent、HTTP 或真实效果实验。继续沿用 3 工具 / 6 请求 / 60 秒及当前授权约束，保留固定 RAG 与单次图用于比较。

## 第 25 步：Agent 配对评测与安全审查（2026-09-28）

- 已完成工程部分：新增 evaluate_agent.py、evaluation/agent_comparison.py、evaluation/agent_metrics.py，共 3 个主要实现文件；增加配对评测/人工复核/安全测试，更新架构、README 和 docs/agent_evaluation.md。不新增工具、模型依赖、业务接口或检索策略，不改 Agent 提示词和循环。沿用 uv.lock，核对 Python statistics、LangGraph 官方文档。
- 可复现设计：同一 dev/资料/构建快照、同一 Chat 和 Embedding 配置、同一精确 cosine 服务，交替两臂先后顺序。逐次认证与索引校验，失效/未配对整对不进入对比。记录代码/样本/资料/映射/向量索引哈希、切块参数、模型配置、原始可获得响应、调用和 trace；默认预检不调用模型，真实运行显式限制最多 15 对。
- 报告口径：分别统计事实正确且有据（人工复核）、资料不足拒答、错误拒答、澄清/技术错误、平均工具与实际模型请求数、mean/P50/P95 延迟和每类样本量、usage/有效价格成本。真实与 fake 不混报；未知 usage/价格不填 0。人工复核任务成功、事实正确和证据支持分开，绑定题目及原始输出 SHA-256，记录复核者/日期/依据，保留胜/负/平和最多三条成功/失败轨迹索引。
- 实际验证：首轮新增 **20 passed（6.58 秒）**；补齐原生适配器预算观测及配对漂移排除后，连同 Agent 循环/单次图/工具/原评测器 **120 passed（37.20 秒）**；最后补充比例和观测字段后新增文件 **22 passed（6.39 秒）**。未执行本步最终状态的全项目测试，不宣称全量通过。ruff check、ruff format --check（104 文件）、uv lock --check（77 包）、git diff --check 通过；未发生测试失败。过程中的初始静态整理发现长行和未使用导入，格式化/移除后通过。
- 安全审查：恶意文档诱使 fake 输出 shell 时后端拒绝；伪造 user_id/kb_id/top_k 在执行前拒绝；A 库检索后读取 B 库 chunk 不泄露正文；规范化重复调用与三个不同查询的循环均受限。另用假金额+合法引用复现现有语义盲点：引用校验会接受真实来源，不代表结论受支持；作为已知限制记录，未偷偷更改策略或声称全面防提示注入。
- **真实对比未运行**：实际预检选中 dev 15 条，全部 draft；缺 DATABASE_URL、JWT_SECRET、OPENAI_API_KEY、EVAL_BEARER_TOKEN 和知识库映射。退出码 2，所有题 not_run，有效配对 n=0，效果、延迟与费用均未知。真实成功/失败轨迹各 0，不补造三例，也不把 fake 测试当真实效果。已通过异步问题请求已复核文件/映射/模糊题标签，未收到补充；未将缺少回答当作人工复核完成。
- 已保存产物：artifacts/validation/step25/ 下 targeted.txt、regression.txt、final-targeted.txt；artifacts/eval/step25-preflight-final/ 下 manifest、results.jsonl、report、review.template.json 和题集快照。早期预检保留为 step25-preflight，后者源代码哈希已过时，最终记录以 final 目录为准。数据与代码哈希见评测说明。产物在 Git 忽略目录，不提交资料/密钥/数据库。
- 结论与限制：保留固定 RAG 为默认；简单事实和确实无资料的问题优先固定流程，查询不明确/需改写/跨文档补读仅为 Agent 候选场景，收益待真实同题复核。当前没有收益证据，不宣称“Agent 改善”或“实验已证明无改善”。同义改写不冒充模糊表述，模糊题标签仍缺失。Agent 预览长度、最新证据替换、可自主 top_k 与整轮截止不同于固定流程，报告显式披露，不能称为仅循环次数不同的消融实验。
- 遗留与下一步入口：本编号任务的真实效果部分等待人工复核和真实评测环境；按 docs/agent_evaluation.md 小规模烟测后执行同一 dev，再独立标注复核并保留结果。没有开始下一编号任务，没有 push、部署或收费调用；不因评测缺口扩大业务修改范围。

## 第 26A 步：React 前端认证与知识库选择（2026-09-29）

- 已完成：新增 frontend 的 React + TypeScript + Vite 应用；主要实现为 api.ts、App.tsx、main.tsx、styles.css、index.html、vite.config.ts 六个文件，另有依赖/类型/测试配置、锁文件及测试。登录、当前用户、授权库列表/单库选择、加载/空状态、失败重试、过期退出和响应式布局已实现。本步无文档管理/聊天，不新增后端业务逻辑、接口或迁移。
- 契约与依赖：先核对需求/架构/进度及真实 FastAPI 路由，再明确权限、401、请求竞态和错误验收。浏览器 /api 经 Vite 代理到现有根路径 /auth/session、/auth/me、/knowledge-bases 与详情；早期规划 /api/v1 未启用。已查 React/Vite/Playwright 官方文档，锁定 React 19.3.0、Vite 8.3.1、React 插件 6.1.1、TypeScript 6.0.2、Playwright 1.63.0，生成 package-lock.json。npm ci、生产构建/类型检查、Prettier 检查均实际通过，无后端依赖变更。
- 认证取舍：令牌只存客户端实例内存；不写浏览器存储/Cookie/URL，不从 JWT 解码信任用户，刷新需重新登录。客户端到期定时器、焦点恢复和请求前检查只控制体验；后端验签/exp/成员检查仍为权限边界。受保护 401 清理所有会话状态；选择时再查详情授权；退出 AbortController + 会话代次阻止迟到响应恢复旧用户，选择序号阻止乱序覆盖。15 秒请求超时，错误中文提示及可获得 request_id，不回显原始异常和密钥。
- 最终浏览器契约回归：**12 passed（9.6 秒）**，真实 Chromium + 模拟 HTTP。覆盖正确/错误登录、JSON/Bearer 契约、无持久存储/刷新登出、空列表、列表失败重试和加载、401清会话、可控时钟过期、撤权后详情拒绝、退出后迟到响应、网络/非法响应、超时、选择乱序、390px 窄屏无横向溢出。
- 真实联调：专用 PostgreSQL + 随机临时数据库 + 真实 FastAPI/Argon2/JWT + Vite/Chromium，**3 项浏览器用例 passed（4.3 秒）**；外层 pytest 夹具 **1 passed（7.57 秒）**，不重复计数。真实登录看到 A 与个人空间且不见 B，使用有效令牌直接请求 B 返回 404；选择、刷新、退出有效。另将一次请求换成正确签名但已过期 JWT，真实后端 401 后前端清空用户；实际登录 TTL 配合浏览器可控时钟验证主动过期。没有替换真实后端响应，没有长时间 sleep 或模型调用。
- 关联后端回归：test_auth.py 与 test_knowledge_bases.py **3 passed（4.14 秒）**。新增 Python 测试夹具 ruff check/format、git diff --check 通过；本步没有执行全项目后端测试，不宣称全量通过。实际检查了登录/授权库页面截图；截图只含本步模拟账号，未记录令牌、密码或请求头，浏览器 trace/视频关闭。
- 故障定位记录：默认沙箱因 WSL 挂载错误不能启动，使用经审批的工作区命令；Docker Desktop 起初未运行，启动后专用库可达。Playwright Chromium 本体下载成功，FFmpeg 下载 TLS 连接重置失败，不录制视频故无需该组件。初始 Vite 就绪探测经环境 HTTP 代理返回 502，按证据为本地测试设置 NO_PROXY；随后发现关闭的 WSL 转发端口探测处于 SYN-SENT，首次真实联调虽通过但耗时 142.60 秒。改为官方支持的 Vite stdout 就绪后，重跑真实联调 7.57 秒通过；不改业务逻辑。一次配置写入因相对路径错误未执行，修正工作目录后完成。
- 中断与恢复：上一轮最后的锁文件重装/回归命令被自动审批用量限制拒绝，未执行。用户继续后已补做 npm ci、构建/格式、12 项浏览器及 3 项后端回归，并补齐 README、docs/frontend_auth.md 与本节。没有将未执行报告为通过。
- 产物与清理：日志、真实联调截图保存在忽略目录 artifacts/validation/step26a/；前端 node_modules、dist、test-results 和 playwright-report 已忽略。fixture 已清理随机库与 API/Vite 进程；停止本步专用 ragdesk-step26a-pg 容器并由 --rm 自动移除，不关闭用户的 Docker Desktop、不操作已有用户数据。未 commit、push、部署或调用收费 API。
- 遗留边界：仅 Chromium 自动验收，未运行其他浏览器或 Windows 原生自动化；PowerShell 命令已编写，实际运行环境为 WSL。内存令牌会随刷新丢失，不抵御任意同源 XSS；退出无服务端撤销，已签发 JWT 到期前仍可能有效。静态部署仍需同源 /api 反向代理，Vite 代理仅作本地开发/预览。当前没有前端注册、刷新令牌或跨标签页登录共享。
- 下一步入口：等待新的编号任务；本步验收目标已通过。启动后端，再在 frontend 执行 npm ci / npm run dev，登录自己的演示账号；空列表按 docs/frontend_auth.md 使用既有授权接口准备库。不会自动进入文档管理或聊天步骤。

## 第 26B 步：文档管理页面（2026-09-29）

- 已完成：在 6 个主要实现文件内新增前端 Documents.tsx，修改 App.tsx、api.ts、styles.css、后端 api/documents.py 和 services/documents.py。提供当前库分页列表、管理员上传/任务状态/失败摘要/重建/确认删除、成员只读与切块预览；空状态、加载、错误重试、知识库切换和窄屏布局齐备。没有增加聊天、权限配置中心、模型调用或检索策略。
- 契约先行：架构新增 26B 约定后定义权限/生命周期测试。新增成员授权 GET 文档 preview，只在 SQL 的同一查询快照读取该库未删除文档当前 active ready 构建，最多 3 块 × 600 字符，含页码/标题/行号与截断标记；没有构建时返回空预览，不在 GET 解析文件。标题最多 6 层 × 160 字符。文档列表/详情新增 latest_job，仅管理员可见；任务详情仍管理员授权，成员不能通过摘要绕过。
- 界面取舍：文档 ready 与最近重建失败分别展示，保留旧有效构建的含义明确；刷新/重新登录后从后端发现任务。上传用 FormData，浏览器生成 multipart boundary；前后端都检查类型/大小，后端继续负责实际格式/权限。预览以纯文本渲染，不执行资料中的 HTML/脚本。沿用内存令牌，不存模型密钥，无新增依赖，两个锁文件保持不变。
- 轮询：每个可见活动任务上次请求结束后等 2 秒，再顺序请求，最多 60 次且单轮最多 120 秒；终态、错误、到期、切库、离开、退出/pagehide 均停止并 abort 在途请求。手动检查可开启新一轮；取消浏览器查询不等于取消后台任务。每页 10 份，未增加跨页缓存/推送或额外监控；任务摘要沿用小规模逐文档 SQL，未宣称大规模性能。
- 最终浏览器回归：**24 passed（18.6 秒）**，真实 Chromium + 模拟 HTTP，包括原认证 12 项与文档 12 项。覆盖成员只读、带物理页码的有界纯文本预览、空/超限/类型上传检查、multipart、任务成功/失败/错误/截止与手动重试、切库/返回/退出停止轮询、在途请求取消和迟到结果隔离、删除取消/确认、重建保留旧索引提示、分页、列表错误恢复、390px 布局。
- 真实后端验收：独立 ragdesk-step26b-pg 容器、随机临时数据库，test_document_preview / test_documents / test_document_lifecycle / test_ingestion_jobs / test_frontend_documents 合计 **26 passed（33.41 秒）**。其中真实浏览器全流程 **1 passed（8.7 秒）**，已计入外层夹具，不重复计算。真实 FastAPI/Argon2/JWT + PostgreSQL + 单 worker + Vite/Chromium，实际上传中文 Markdown→202→worker 发布→预览数字/型号→重建切换 build→成员预览及直接写接口 403→外库 404→管理员删除→旧预览 404。Embedding 明确 fake，无真实模型/收费 API 请求。
- 预览回归另覆盖未完成空预览、失败重建仍用旧 build、成功后新 build/新 chunk、成员看不到管理任务摘要、撤权/删除/错误库不可见、块数量/正文长度/标题截断边界。沿用原删除竞争与任务发布回归；未执行后端全项目测试，不报告全量通过。
- 故障定位：初次后端 24 passed、1 failed，新测试错误期待成员删除 204，核对实际契约为 200 后仅改断言。浏览器首轮 20 passed、3 failed，定位选择框可访问名称和异步断言时序；复跑显示模拟任务默认 queued 与“处理中”断言不一致，修正夹具为 running 后 23 passed，再补在途取消验收后最终 24 passed。没有随机修改后端业务逻辑。一次脚本因无 python 别名未执行实现部分，改用已有 python3；ruff 长行先格式化解决。最终 Prettier 提示真实测试文件格式，再格式化后复查通过。
- 中断与恢复：上轮末尾的日志读取/测试修正命令被自动审批额度限制拒绝，未执行。用户继续后重新核对磁盘结果，确认此前真实联调已完成，补执行修正和最终回归；没有把中断命令报告为成功。
- 工程验证：最终 TypeScript + Vite 生产构建通过；Prettier 全前端检查通过；本步 Python 4 文件 ruff check/format 通过；uv lock --check（77 包）通过；git diff --check 通过。已查看真实页面截图并修正上传标签换行；截图为微调样式前的真实联调产物，功能行为相同。React useEffect、AbortController、FormData 官方文档已查询，参考链接与 PowerShell 启动/验收命令见 docs/frontend_documents.md；README/architecture 已同步。
- 产物与边界：安全日志和模拟资料截图位于忽略目录 artifacts/validation/step26b/。夹具清理随机数据库、API/worker/Vite 进程；不提交上传资料或数据库，不 push/部署。自动测试仅 Chromium，PowerShell 命令在文档提供，未在 Windows 原生执行。纯扫描件/复杂 PDF 仍按已有解析契约报错，无 OCR；仅展示已发布切块，不宣称完整解析审阅或真实 RAG 效果。
- 下一步入口：本步目标已验收，等待新的编号任务。按 docs/frontend_documents.md 启动 API、唯一 worker 与前端即可演示；没有自动进入聊天或其他功能。
- 环境清理核对：续作收尾执行 docker ps 时 Docker Desktop Linux daemon 的命名管道不存在，不能确认此前 --rm 专用容器是否已移除；未为清理而擅自重启用户 Docker Desktop。随机数据库和测试应用进程已由成功退出的夹具清理。若之后发现容器仍在，可执行 `docker stop ragdesk-step26b-pg`；只操作本步专用容器。此状态不影响已记录的实际测试结果。

## 第 27 步：单轮问答界面（2026-09-29）

- 已完成：新增 Question.tsx 和 services/agent_answers.py，修改前端 api.ts/App.tsx/styles.css 与后端 api/answers.py，合计 6 个主要实现文件；另更新依赖/锁文件、测试和文档。支持当前库输入问题、默认固定 RAG/可选 Agent、状态/Markdown 正文/引用、点击查看受保护原文、Agent 简洁事件、资料不足/澄清/超时/权限失效等状态。没有 SSE、逐 token 输出、聊天历史或跨轮记忆，也没有新增工具/Agent 策略。
- 契约先行：阅读规则/需求/架构/进度，核对发现 Agent 只有函数入口，先在架构约定 POST /knowledge-bases/{kb_id}/answers 的可选 mode（默认 rag）、安全事件与错误形状，再写 HTTP 权限/错误测试。复用现有 run_agent 的 3 工具/6 模型请求/60 秒、最终引用校验，新增服务只做授权、上下文注入、trace/响应投影和返回前来源复核。额外 user_id/history/工具预算与未知模式均拒绝；Agent 不接受非默认 top_k，内部仍自行选择允许范围内的候选数。
- 来源与安全：正文/引用来自后端已校验结果；HTTP 返回安全事件最多 3 条、查询摘要最多 160 字，不暴露完整图状态、工具原文或思维链。点击引用由当前库和 document/build/chunk UUID 构造固定路径，不访问任意 source_path；重新鉴权并校验有效构建。404/410 时清空此前打开的原文，不回退旧片段。合法引用仍不自动证明语义支持或事实正确。
- 界面生命周期：每次只发送 question/mode，新问题清空旧结果/来源/事件；切模式取消当前请求，切库还清空问题，离开/退出取消在途请求并通过序号拒绝迟到回调。客户端问答上限 75 秒，其他接口仍 15 秒；不自动重试问答。取消浏览器等待不承诺撤回后台/供应商调用，固定 RAG 的原有模型超时/重试机制不变。工具事件在普通响应结束后统一展示，未伪装实时进度。
- 渲染/依赖：查阅 React useEffect、FastAPI response model、react-markdown 官方文档；安装并精确锁定 react-markdown 10.1.0，peer 要求 React/类型 >=18，与项目 19.3 兼容，新增 83 个锁条目；通过比较前后 package-lock 确认既有包版本无变化。使用 skipHtml，无原始 HTML 插件或 dangerouslySetInnerHTML；普通链接/图片仅文本，不自动请求外部资源，原文使用纯文本。后端依赖未变化。
- 首轮验证：新增 Agent HTTP + 原固定问答/trace 共 **46 passed（19.92 秒）**，出现 6 条 Pydantic 序列化警告，定位为响应扩展时 asdict 把 Citation 转为 dict，改为保留 Citation 对象；未更改校验策略。新增测试工厂字符串的一处长行由静态检查发现并修正。无失败测试，不以警告掩盖类型问题。
- 浏览器回归：**36 passed（30.3 秒）**，真实 Chromium + 控制 HTTP/时钟，包括原登录/文档 24 项和问答 12 项。覆盖固定/Agent、独立请求体（无 history）、Markdown 排版、原始 HTML/脚本/外链/图片不执行不请求、授权来源实际页码/标题/原文、404/410 清旧片段、不足/澄清、模型校验错误、504、安全 Agent 错误事件、75 秒等待与不重试、切库取消及迟到回答隔离、换模式清来源、空/超长输入、撤权/401 退出、390px 页面无横向溢出。
- 最终后端与真实闭环：专用 ragdesk-step27-pg、随机临时库，test_agent_http / test_answers / test_traces / test_agent_loop / test_frontend_question 合计 **74 passed（36.59 秒）**，最终无序列化警告。其中新 HTTP 11 项；另真实浏览器全流程 **1 passed（3.6 秒）**，已包含在外层夹具，不重复计数。真实 FastAPI/JWT/数据库/Agent 循环/Vite/Chromium，模型仅在临时测试应用显式注入 fake：同用户固定 RAG→Markdown 与授权原文→Agent→1 工具/4 模型请求记录→切空库清状态并不足→未授权库 404。补验监督器截止：504、trace 不完整、未知 usage/调用观测/费用仍 null。没有真实模型接口或费用调用，不能报告问答效果/Agent 收益。
- Trace：沿用受保护存储与查看接口，复制已完成或监督器快照的模型/候选/事件，不与仍运行线程共享可变对象；按 HTTP 的有效价格配置重算可估费用。未完成模型观测标 unknown，已知 Agent 请求尝试计数单独记录。权限/证据失效时 HTTP 不返回此前工具摘要；私有 trace 仍按原授权保护。
- 工程检查：最终 TypeScript + Vite build、全前端 Prettier、4 个本步 Python 文件 ruff check/format、uv lock --check（77 包）、git diff --check 均通过。已检查真实单轮问答截图。没有执行后端全项目回归，结果仅指上述相关测试；浏览器仅 Chromium，PowerShell 命令已提供但未在 Windows 原生执行。
- 文档/清理：README、architecture、docs/frontend_question.md 同步了接口请求/响应、启动/验收命令、安全与取消边界和面试追问。日志与模拟截图在忽略目录 artifacts/validation/step27/，不记录密码/令牌/浏览器 trace 或视频。成功夹具清理随机数据库与测试应用进程，专用 ragdesk-step27-pg 已停止并通过 --rm 自动移除；不关闭用户 Docker Desktop，不操作已有用户数据，没有 commit/push/公网部署。
- 遗留边界与下一步入口：真实模型问答/tool calling 效果未验证；来源有效不等于结论正确。当前只支持基础 CommonMark，不增加表格/高亮插件。配置真实后端与兼容索引后可从“开始问答”使用；缺少模型配置明确报错，不自动换 fake。等待新的编号任务，SSE 若需要作为下一小步单独定义。
