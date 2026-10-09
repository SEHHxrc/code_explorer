# Code Explorer 架构与代码接口说明

本文描述当前代码边界与实际入口。各目录的文件、类和依赖关系见对应 `README.md`。

JS/TS 已增加公共复杂解构与 Vue/React/Node 核心规则，实际覆盖边界以 `SECURITY_COVERAGE.md` 为准，不表示全量安全分析。

## 1. 系统边界

```text
Vue 3 / Vite / Element Plus
├─ 项目导入与概览
├─ 文件树、符号栏与 Sigma.js 依赖图
├─ 项目智能体与工具证据
├─ 依赖图盲态配对实验
└─ Docker 隔离执行与安全扫描
              │ HTTP / 可续传 SSE
              ▼
FastAPI
├─ Projects
│  ├─ ProjectImportService（导入、发布与持久化编排）
│  ├─ ProjectQueryService（库存、占用与快照）
│  ├─ ProjectDeletionService（隔离、删除、补偿与恢复日志）
│  ├─ ProjectWorkspaceService（Git/ZIP、清洗与受控文件系统）
│  └─ ProjectAnalysisPipeline（纯源码分析）
│     ├─ UnifiedCodeAnalyzer（Tree-sitter + NetworkX）
│     ├─ SecurityAnalysisService（通用安全 IR + 规则包 + 结构路径）
│     └─ ProjectManifestBuilder / build_repo_map
├─ ProgramIdentity（依赖图与安全分析共享文件、符号、位置、调用点身份）
├─ SyntaxAnalysis（共享语言目录、Tree-sitter Parser 池和节点工具）
├─ ProgramGraphService（八种依赖分析语言的公共控制 IR、函数级 CFG、到达定义）
├─ AgentQueueWorker → AgentRunManager
│  ├─ ProjectContextBuilder
│  ├─ ModelProvider（OpenAI Responses 或 OpenAI 兼容 API）
│  └─ 只读 ToolRegistry
├─ ExperimentComparisonService（静态安全证据 / 原始文件对照）
└─ ExecutionService → ExecutionWorker → DockerExecutor
```

通用 Middleware 只处理异常脱敏、安全响应头和响应大小，不改写领域 DTO 或 SSE。依赖图交换格式由 `GraphExchangeNormalizer` 在服务边界显式生成。

## 2. 模块职责与文档入口

| 模块 | 职责 | 详细说明 |
| --- | --- | --- |
| 后端应用根 | FastAPI 装配、数据库初始化和 Worker 生命周期 | `backend/app/README.md` |
| HTTP API | 请求、用户上下文、错误映射和 SSE | `backend/app/api/README.md` |
| 项目功能域 | 导入、CRUD、Artifact、库存/快照和可恢复删除 | `backend/app/services/projects/README.md` |
| 项目分析 | 对已存在源码目录执行无持久化副作用的分析流水线 | `backend/app/services/project_analysis/README.md` |
| 安全工作区 | Git/ZIP 获取、路径策略、清洗、发布和恢复 | `backend/app/services/project_workspace/README.md` |
| 依赖分析器 | 多语言语法提取与跨文件关系解析 | `backend/app/services/dependency_analyzer/README.md` |
| 语法基础设施 | 共享语言目录、Tree-sitter Parser 池和节点读取 | `backend/app/services/syntax_analysis/README.md` |
| 公共值绑定 | 对象、序列和默认/rest 模式投影 | `backend/app/services/value_binding/README.md` |
| 程序身份 | 统一文件、符号、源码位置和调用点 ID | `backend/app/services/program_index/README.md` |
| 最小程序图 | 八种依赖分析语言的公共控制 IR、CFG 与到达定义 Overlay | `backend/app/services/program_graph/README.md` |
| 静态安全证据 | 跨语言前端、通用 IR、规则包、候选路径和证据协议 | `backend/app/services/security_analysis/README.md` |
| 大模型接入 | 配置、Provider 抽象与 HTTP 协议 | `backend/app/llm/README.md` |
| 项目智能体 | 上下文、模型—工具循环、队列与事件 | `backend/app/agents/README.md` |
| 只读工具 | JSON Schema、参数校验、源码/图查询 | `backend/app/agents/tools/README.md` |
| 对照实验 | 静态安全证据/原始文件盲态配对与指标 | `backend/app/experiments/README.md` |
| 隔离执行 | 策略、队列、Docker Worker 和审计 | `backend/app/execution/README.md` |
| 前端功能 | 项目、图、实验和执行页面 | `frontend/src/features/README.md` |
| 前端通信 | Axios 与 SSE 客户端 | `frontend/src/services/README.md` |

分析产物写入 `backend/storage/artifacts/<project_id>.json`，其中保留 Manifest、Repo Map、原始依赖图和符号证据。SQLite 保存项目元数据、Agent 队列/事件、实验记录和执行队列/事件。

## 3. 项目导入、分析与事务回滚

1. `POST /api/projects/analyze` 把 Git URL 或 ZIP 转换为 `AnalyzeProjectCommand`。
2. `ProjectImportTransaction.begin()` 创建受控暂存操作并登记补偿。
3. `ProjectWorkspaceService.prepare()` 获取来源并由 `ProjectSanitizer` 清理链接、敏感文件、超大文件、禁止类型和噪声目录。
4. `ProjectAnalysisPipeline.analyze()` 对暂存源码执行纯分析；它内部调用依赖分析、安全分析和确定性派生，不读取用户或数据库。
5. `UnifiedCodeAnalyzer.run_full_analysis()` 按采集、索引、导入、类型和图构建五阶段产生原始依赖图。
6. `SecurityAnalysisService.analyze()` 复用依赖分析文件范围和调用边，生成安全结构证据；不重复持久化完整依赖图。
7. 工作区发布后，项目记录和分析产物依次持久化。
8. `GraphExchangeNormalizer.normalize()` 生成有版本、有限额、路径安全的公开图 DTO。
9. 全部成功后提交事务；异常会逆序删除本次产物和工作区，未完成补偿交给 `WorkspaceJanitor`。

`ProjectRepository` 只管理 `ProjectModel`：`create()` 是纯新增，`update_owned(ProjectUpdate)` 只修改
白名单字段。项目删除先隔离工作区和 Artifact，再由 `ProjectDeletionRepository` 在一个数据库事务
中删除跨功能域关联记录；提交前失败会恢复资源，进程中断由 `ProjectDeletionJournal` 和
`ProjectDeletionJanitor` 恢复或清理。

Manifest 与 Repo Map 是模型事实底座。模型只负责解释和归纳，不应替代静态分析器制造不存在的文件、符号或入口点。

## 4. 大模型与智能体调用链

模型配置由 `get_model_configuration()` 读取以下环境变量：

- `CODE_EXPLORER_LLM_PROVIDER`
- `CODE_EXPLORER_LLM_MODEL`
- `CODE_EXPLORER_LLM_API_KEY`
- `CODE_EXPLORER_LLM_BASE_URL`

`create_model_provider()` 对 OpenAI 创建 `OpenAIResponsesProvider`，对 Ollama、vLLM 或自定义兼容服务创建 `OpenAICompatibleProvider`。普通文本入口是 `generate(instructions, prompt)`；工具入口是 `generate_with_tools(instructions, prompt, tools)`。

模型配置状态、真实连通性和模型目录分别由 `GET /api/models/status`、`POST /api/models/probe`
和 `GET /api/models` 提供。探测接口由用户在智能体页面显式触发一次最小函数工具请求，同时验证
文本生成与 Agent 工具协议；智能体选择的模型作为单次运行参数进入持久化队列，不修改环境变量
默认值。上游 HTTP 错误只保留安全状态码、错误类型/代码、`Retry-After` 和请求 ID。

模型输入使用 `CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS` 作为供应商无关的近似字符预算，输出使用 `CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS`。字符预算会覆盖系统指令、工具 Schema、静态项目事实与多轮工具观察的组合，但不代表也不能改变模型自身的 Token 上下文窗口。`model.started` 事件只记录提示、工具和输出预算的数量，不持久化请求正文；第三方网关在长输入时返回 5xx 或重置连接，可据此调低字符预算。

项目数据库是后端资源的权威索引。数据管理页通过 `ProjectQueryService` 统计工作区和分析产物，
并从持久化文件树、Manifest 与依赖图恢复前端状态；完整删除经过 `ProjectDeletionService`，活动
任务会阻止删除。

智能体流程：

1. `POST /api/agent/projects/{project_id}/runs` 创建运行和持久化队列项。
2. `AgentQueueWorker` 原子认领并续租；独立进程入口为 `python -m backend.app.agents.worker`。
3. `ProjectContextBuilder.build()` 从 Manifest 和 Repo Map 生成有界上下文。
4. `AgentRunManager` 将 `ToolRegistry.schemas()` 与上下文交给 `ModelProvider.generate_with_tools()`。
5. 模型返回 `ToolCall(name, arguments)` 时，注册表先用 Pydantic 严格模型验证参数，再执行匹配的只读工具。
6. 工具内容、证据和截断标记作为 `TOOL_OBSERVATIONS` 进入下一轮；达到 `max_steps` 后进行最终汇总。
7. 请求、工具、证据、文本和终态都持久化为单调序号事件，前端可用 `after=<sequence>` 续传 SSE。

工具包括项目 Manifest、入口点、符号搜索、有限源码读取、依赖邻居和有限全文搜索。模型不能调用未注册函数、Shell 或 Docker。

## 5. 依赖分析器

`services/dependency_analyzer` 使用 Tree-sitter 解析 Python、JavaScript/TypeScript、Go、Java、C/C++ 和 Rust，再用 NetworkX 组装图。

- `handlers/` 将单语言语法节点提取为 `Definition`、`Reference` 和 `ImportRec`。
- `CollectionPhase` 并行解析文件并合并 `FileContext`。
- `IndexingPhase` 建立文件、模块、限定名、短名和成员索引。
- `ImportResolutionPhase` 解析本地模块、标准库和第三方模块。
- `TypeResolutionPhase` 解析继承、类型、MRO、成员和可调用目标。
- `GraphResolutionPhase` 生成节点以及 contains、declares、imports、calls、inherits、overrides 等关系。
- `UnifiedCodeAnalyzer` 是唯一公共门面，提供 `run_full_analysis()` 和 `get_progress()`。

依赖图调用边保存精确调用位置、`callsite_id`、置信度、解析方法、may/must 语义和截断状态。`callsite_id` 由共享 `ProgramIdentity` 只根据源码位置生成：同一调用点解析出的多个候选目标共享调用点身份，但各自保留不同边 ID。安全分析通过该身份引用已有调用关系，不再创建第二套 caller→callee 图。

分析器不执行项目代码。语言内置、标准库和第三方依赖使用独立节点类别，前端可按层级筛选或临时隐藏。

## 6. 静态安全证据基础设施

`services/program_graph` 是面向多语言 CFG/PDG 的最小 CPG 公共层。Python、Java、JavaScript、TypeScript、Go、C、C++ 和 Rust 全部继承同一个 `ProgramGraphFrontend` 文件编排模板，并进一步继承 `TreeSitterProgramGraphFrontend`；语言差异只保留在 collector/adapter 钩子中。`ControlFlowGraphBuilder` 统一处理顺序、分支、循环和突然退出，`ReachingDefinitionsPass` 使用同一个工作列表算法生成变量级数据依赖，`ValueFlowPass` 消费八种语言一致的声明/赋值配对、复合赋值旧值、调用实参与接收者。公共 `AccessPathResolver` 将各语言成员和静态下标表达式转换为稳定变量身份。每个函数同时保存可连接现有调用图的 `symbol_id` 和防止重载或重复绑定互相覆盖的 `method_id`。细粒度函数图不进入前端依赖图。

`services/syntax_analysis` 是依赖图与 ProgramGraph 之间更底层的共享层，唯一维护源码扩展名目录、忽略目录、线程本地 Tree-sitter Parser 池、节点字段/文本读取和类型文本归一化。两个图仍分别拥有自己的中间记录与边类型：依赖图负责跨文件符号和调用目标，ProgramGraph 负责函数内 CFG 和到达定义。当前不跨阶段常驻完整 AST；若性能测量证明重复 parse 是瓶颈，再增加按内容哈希、生命周期有界的语法树缓存。

`services/graph_core` 使用 `typing.Protocol` 定义 `GraphArtifactView`，通过能力声明区分宏观依赖图和函数分区 ProgramGraph。现有 NetworkX node-link 依赖图与 Pydantic ProgramGraph 由只读适配器接入，因此公共校验可以检查重复身份和悬空边，而不要求两个图继承同一存储基类，也不改变持久化格式。

`services/semantic_index` 使用独立的 `SemanticIndexView` 和 `SemanticValueFlowView` 接口共享解析事实，而不创建第三张图。依赖分析器在同一次 Tree-sitter 解析后投影可调用对象、调用点、候选目标、变量类型及解析不确定性；ProgramGraph 提供器继续追加形参、`return` 和调用结果槽位。项目分析将增强后的 1.1 索引作为独立 JSON 产物持久化。跨过程安全数据流优先查询此索引，旧产物缺失索引时才兼容回退到依赖图边与 ProgramGraph 节点；CFG 和 reaching-def 仍由 ProgramGraph 独立负责。

该公共层已经由安全分析内部消费，但不作为独立完整产物加入默认项目导入结果。
安全服务只为已注册安全规则的语言请求 ProgramGraph，避免额外解析不可能生成 Source/Sink
事实的项目文件。Python 专用名字传播实现已经删除；所有依赖分析语言均已通过函数身份、
分支、循环、到达定义和调用点参数契约验证。后续语言安全规则只产生事实并复用同一数据流分析器。

`services/security_analysis` 把语言语法解析、语言语义、规则知识和跨函数路径分开：`LanguageFrontend` 生成通用 `SecurityProgramIR`，`LanguageSemantics` 提供参数绑定等语言语义，分层 `RulePack` 描述安全入口、Source、Sink、Guard 与 Sanitizer，通用规则引擎只消费这些抽象。当前默认注册 Python、Java、Go、C/C++、JavaScript/TypeScript 前端；Java 接入 Spring Web/Servlet，Go 接入标准库及 `net/http`，C/C++ 共用标准库/POSIX/SQLite 规则，JS/TS 共用浏览器 DOM、Node.js 与 Express 首批规则。除 Python AST 前端外均复用 `TreeSitterSecurityFrontend`；普通位置参数绑定复用 `PositionalLanguageSemantics`，JS/TS 对 spread 保守降级。未注册语言（目前 Rust）明确进入覆盖率与诊断，不能计为已扫描。

`SecurityEvidencePack 2.3` 使用顶层 `entrypoints`、`facts`、`call_edges`、`dataflows` 和 `snippets` 注册表，候选项只保存 ID 引用，避免重复保存路径和片段。证据包保存精确位置、不确定性、规则包版本、覆盖率和分析局限，不包含完整依赖图、Repo Map、自然语言架构概述或系统生成命令。数据流统一使用 `program_graph` 的公共 CFG、到达定义和变量感知 `value_flow` Overlay，沿已解析调用执行有限深度的实参到形参及直接返回值传播；绑定由 `LanguageSemantics` 提供。八种程序图语言已对齐语法变量路径能力，可区分 `obj.field`、`payload["key"][0]`、`values[0]`，但不代表堆身份敏感分析。安全规则覆盖其中七种语言，Rust 仍报告缺口。JS/TS 的匿名回调、平台 API 遮蔽和属性读写复用公共程序图，异步时序、闭包、复杂解构及堆别名未完整建模。动态下标、嵌套调用返回、路径条件、控制依赖和完整对象传播仍属于后续阶段。

C/C++ 库 API 使用头文件与词法名称过滤，未执行完整预处理或编译器类型绑定；LLM 端点匹配
置信度为 `medium`，相关局限进入候选和证据头。`system/popen` 是隐式 Shell，`exec` 只匹配
可执行文件路径，`printf` 系列只匹配格式参数，`sqlite3_exec` 只匹配第二个 SQL 参数。
缓冲区 Source 显式保存输出实参位置与未建模诊断，不能以读取数量/状态码替代内容传播。

安全功能域的依赖方向保持 `projects → project_analysis → security_analysis`；安全模块复用
`program_graph` 的 CFG/DFG 和 `semantic_index` 的已解析目标。`syntax_analysis` 保存纯语法共性，
包括 C/C++ 声明符读取。数据库、产物管理、项目删除、模型请求不进入安全前端或规则包。

`SecurityEvidencePromptBuilder` 将完整产物投影为 `LLMSecurityEvidenceEnvelope 1.2`：按问题、真实数据流、严重性和置信度选择候选，展开 Source/Sink、赋值、调用与返回边界，保持有效 JSON，并通过 `omitted_findings` 显式报告预算省略。普通 Agent 初始上下文使用该格式，也可以通过只读工具分页获取剩余候选。

## 7. 隔离执行与安全扫描

执行请求由 `ExecutionTaskRequest` 描述为 `argv` 数组，不能提交宿主 Shell 字符串。`ExecutionPolicy` 先验证镜像白名单、扫描 Profile 和资源上限，`ExecutionService` 只负责入队。独立 `ExecutionWorker` 再按当前策略复核计划，最后调用 `DockerExecutor`。

容器禁网、项目只读挂载、根文件系统只读、非 root、丢弃 capabilities，并限制 CPU、内存、PID、时间和输出。执行模块不作为 Agent 工具注册，因此模型不能自行启动容器。完整配置见 `docs/EXECUTION.md`。

## 8. 前端架构

`features/project-insight/ProjectInsight.vue` 是工作台总入口，使用异步组件和 `KeepAlive` 切换概览、代码与图、Agent、实验和执行页面。项目状态由 `useProjectAnalysis()` 管理，删除或重新导入时先中止在途请求并销毁旧图实例。

依赖图把后端 DTO 转换为 Graphology 图，只对可见子图运行 ForceAtlas2 Worker 和 NoOverlap；Sigma.js 负责相机、拖拽和选择。所有节点大小统一，类别通过颜色和标签表达。右侧符号栏调用 `revealSymbol(target)` 恢复完整图并聚焦对应节点。

组件只通过 `src/services` 访问后端。Axios 处理普通 HTTP，`consumeSse()` 处理 JSON SSE 和取消信号。

## 9. 持久化模型与契约

主要数据库模型：

- `ProjectModel`：已发布项目元数据和文件树。
- `AgentRunModel`、`AgentJobModel`、`AgentEventModel`：Agent 结果、队列租约和事件。
- `ExperimentComparisonModel`、`ExperimentReviewModel`：盲态比较与评审。
- `ExecutionTaskModel`、`ExecutionEventModel`：容器任务、资源计划和审计事件。

主要公开契约：

- `ProjectManifest`、`Entrypoint`、`Evidence`：确定性项目事实。
- `DependencyGraphDTO`：版本化依赖图交换格式。
- `SecurityEvidencePack 2.3`：去重后的静态安全事实、结构路径、函数内/跨过程 Def-Use、片段、覆盖率和不确定性。
- `ProgramGraphArtifact 1.0`：按函数分区的最小 CPG、CFG 和到达定义内部协议，当前不属于公开 API。
- `LLMSecurityEvidenceEnvelope 1.2`：按预算生成的自包含模型安全证据。
- `AgentRunRequest`、`ContextPacket`、`ToolResult`、`AgentRunView`：智能体边界。
- `ModelResult`、`ModelTurn`、`ToolCall`、`ProviderCapabilities`：模型适配边界。
- `ExecutionTaskRequest`、`ExecutionPlan`、`ExecutionTaskView`：隔离执行边界。

## 10. 安全与扩展边界

- 外部项目先进入暂存目录，路径、链接、文件类型和大小都受策略约束。
- 源码工具只能访问解析后的项目根目录；读取、搜索、耗时和输出均有限额并进行敏感信息脱敏。
- 仓库内容、README、注释、模型输出和工具输出都不具备指令权限。
- API Key 不写入公开状态；上游错误正文不直接返回客户端。
- SSE 使用持久化递增游标；Agent 和执行任务支持跨进程取消。
- Docker 仍不是虚拟机安全边界；生产环境应隔离 Worker 主机并配置守护进程级 seccomp/AppArmor。
