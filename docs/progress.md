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
