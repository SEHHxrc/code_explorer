# Code Explorer 架构与代码接口说明

本文描述当前代码边界与实际入口。各目录的文件、类和依赖关系见对应 `README.md`。

安全分析的语言适配目录现为 `security_analysis/language_adapters`，不是网页前端；公共安全 IR 定义于
`security_analysis/ir.py`，原 frontends 只兼容再导出。`syntax_analysis.session` 为一次生产流水线
复用有限语法树；原始字节仍由消费者读取和检查，Tree 复制交付，任务结束释放，不改变规则或实验预算。

JS/TS 已增加公共复杂解构与 Vue/React/Node 核心规则，实际覆盖边界以 `SECURITY_COVERAGE.md` 为准，不表示全量安全分析。

ProgramGraph 1.18 的条件表达式、控制依赖和 `PathConditionQuery` 属于公共程序语义域：
语法前端仅提取条件/赋值，NetworkX 计算支配关系，Z3 核验有界标量路径与必要条件反例。
安全域只对已选值流调用查询并保存/交付结果，不维护第二套证明器。
`program_graph.path_composition` 把现有参数/直接返回路线编成有限临时 CFG，复用同一求解器；
`flow_analysis.condition_routes` 只提供真实激活和绑定，不增加新调用解析。未知堆/事件边界仍 unknown。
安全私有 CFG 完成改写后重新计算控制依赖，避免使用原始图过期的后支配关系。

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

分析基础设施现按职责分层：公共语法/身份/值绑定 → 可选原生工具与语义事实 → 依赖图/程序图 →
安全规则与证据 → 项目分析编排。`language_backends` 只输出 `ProjectSemanticResult`，不直接修改图；
依赖分析通过公共 `project_resolvers()` 装配，亦可注入 `ProjectSemanticResolver`。
通用阶段观测在 `core.progress`，公共访问路径在 `value_binding.access_paths`。
移动实现保留薄兼容入口，内部调用新归属；旧非原生解析完整保留，原生失败明确回退，不维护两套传输/解码实现。

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
| 原生语言后端 | Go/TypeScript 工具、环境白名单、进程传输与协议解码 | `backend/app/services/language_backends/README.md` |
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

`services/syntax_analysis` 是依赖图与 ProgramGraph 之间更底层的共享层，唯一维护源码扩展名目录、忽略目录、线程本地 Tree-sitter Parser 池、节点字段/文本读取和类型文本归一化。两个图仍分别拥有自己的中间记录与边类型：依赖图负责跨文件符号和调用目标，ProgramGraph 负责函数内 CFG 和到达定义。session 按内容哈希在同一任务内有界复用树，退出释放，不把完整 AST 长期常驻或写入持久化产物。

`services/graph_core` 使用 `typing.Protocol` 定义 `GraphArtifactView`，通过能力声明区分宏观依赖图和函数分区 ProgramGraph。现有 NetworkX node-link 依赖图与 Pydantic ProgramGraph 由只读适配器接入，因此公共校验可以检查重复身份和悬空边，而不要求两个图继承同一存储基类，也不改变持久化格式。

`services/semantic_index` 使用独立的基础与可选查询协议共享解析事实，而不创建第三张图。依赖分析器在同一次 Tree-sitter 解析后投影可调用对象、调用点、候选目标、变量类型及解析不确定性；ProgramGraph 提供器继续追加形参、`return` 和调用结果槽位。基础产物为 1.0、值槽为 1.1；原生声明类型的 1.2 产物保留精确位置、角色和同名声明候选，兼容旧产物。项目分析独立持久化增强后的索引。位置查询不推断变量使用点绑定；跨过程安全数据流优先查询此索引，旧产物缺失索引时才兼容回退到依赖图边与 ProgramGraph 节点；CFG 和 reaching-def 仍由 ProgramGraph 独立负责。

该公共层已经由安全分析内部消费，但不作为独立完整产物加入默认项目导入结果。
安全服务只为已注册安全规则的语言请求 ProgramGraph，避免额外解析不可能生成 Source/Sink
事实的项目文件。Python 专用名字传播实现已经删除；所有依赖分析语言均已通过函数身份、
分支、循环、到达定义和调用点参数契约验证。后续语言安全规则只产生事实并复用同一数据流分析器。

### 6.1 处理流程与职责

安全分析不是另一套依赖图，也不是仅靠调用关系判断危险。它把语法观察、安全规则和静态值传播分开，再组装可核验的候选证据：

```text
project_analysis/AnalysisPipeline（调度，不拥有安全规则）
  ├─ dependency_analyzer → 已解析调用/实例化目标 → semantic_index
  └─ security_analysis/SecurityAnalysisService
       ├─ scanner → language_adapters → SecurityProgramIR → rule_engine + rules
       │                                     └─ 入口 / Source / Sink / Guard / Sanitizer
       ├─ program_graph → 公共 Control IR → CFG → 到达定义 → 值流
       ├─ flow_analysis ← 安全事实 + 公共图 + semantic_index + 语言参数语义
       │                 └─ 有界函数调用、返回值、初始化字段、工厂变体和值边界
       └─ 候选、原始位置、片段、覆盖与能力前沿 → SecurityEvidencePack
                                                        ↓
                  SecurityEvidenceSelector → SecurityEvidenceCatalog
                                                        ↓
                  Agent 只读工具 → LLM 核验 / 发现台账 / 报告
```

| 层次与入口 | 拥有的内容 | 不应承担的内容 |
| --- | --- | --- |
| `syntax_analysis`、`program_index`、`value_binding` | 语法共性、稳定身份、参数/解构投影 | 漏洞判断、模型调用 |
| `dependency_analyzer`、`semantic_index` | 结构关系、调用点与目标、解析依据、参数/返回接口 | 重算函数内 CFG、把结构路径冒充污点链 |
| `program_graph` | 八语言共享控制 IR、CFG、到达定义和值流、操作级存储事实与值查询后端选择；当前格式 1.15 | 安全规则、项目存储、LLM 输入选择；当前不是完整 PDG/CPG |
| `security_analysis/language_adapters`、`ir.py` | 把代码/API/框架语法转换为规则可消费的事实输入；旧 frontends 仅兼容再导出 | 各语言分别实现污点求解器 |
| `rules`、`rule_engine.py`、`scanner.py` | 版本化入口、Source/Sink 与防护观察匹配、语言扫描覆盖 | 仅凭 Sink 命中确认漏洞或凭 Sanitizer 名称截断污点 |
| `semantics` | 参数位置、关键字、展开和接收者绑定 | AST 再解析、风险结论 |
| `flow_analysis` | 唯一公共传播算法、私有 Overlay、对象/工厂返回、调用上下文与传播前沿 | 新增未经依赖层解析的结构调用边、执行项目 |
| `service.py`、`path_finder.py`、`slicer.py` | 有界候选、结构线索、原始位置和附近源码片段、产物组装 | 完整反向切片、存储事务或语义漏洞证明 |
| `evidence_selection.py`、`evidence_query.py` | 路径目录、优先级、ID 解引用及无损分页 | 重扫项目、生成新关系或修改历史证据 |

Python 安全前端使用 Python AST，其余注册语言主要复用 Tree-sitter 安全前端；程序图前端则统一走 Tree-sitter 模板。两类 IR 的职责不同，不应直接合并成一个安全/图混合类。安全规则当前覆盖七种语言，Rust 明确报告未注册规则，不计为完成安全扫描。

### 6.2 对象字段与多类型工厂

`InstanceSyntax` 的语言适配提取接收者、所属类和构造器，并声明对象引用赋值能力。`objects.py` 复用公共参数绑定和 `ValuePathQuery` 查询构造器正常出口字段来源；`factory_returns.py` 自底向上保留 `FactoryVariant`，同一工厂的不同具体类型不合并字段状态。`object_contracts.py` 唯一拥有 LocalInstance、ObjectBinding 与 ObjectReferenceResolver 结构协议；`instance_common.py` 维护引用赋值判定、逃逸/未知修改拒绝及图重算。公共字段槽和类型子槽渲染唯一位于 `value_binding.objects`。

`object_references.py` 实现 ObjectReferenceResolver，在公共 CFG 上计算已有局部分配的有界引用环境，直接别名共享分配位置字段身份；根变量重绑定不删除其他别名持有的旧字段。ProgramGraph 1.10 的 `definition_object_slots/use_object_slots` 经公共值身份协议进入原到达定义和值流 Pass，新 Overlay 不重复保存字符串身份表；旧 1.9 精确值表仍兼容。证据直接消费结构化分配，不反解新字符串身份，不新增语言私有污点引擎。分支歧义、重复分配、未知副作用或超限不冒充唯一对象；原始变量名与别名赋值位置保留在证据中。

方法读取通过 `ProgramCallSite.receiver_field_bindings` 绑定兼容类型的字段槽位，并只过滤已有调用目标；抽象基类的额外目标不冒充具体子类状态，也不按同名方法补边。普通字段读取保留可能值并集，父字段和整对象覆盖复用公共到达定义规则。返回边界核对调用点，避免两个工厂调用串值。字段证据保留实际类型、分配、初始化位置和原调用边。

ProgramGraph 1.11 的 `ObjectRootEffect` 约束对象根的入口字段、实际写入、正常出口确定/可能写入，
`object_effects.py` 让对象参数与接收者共用此接口。前端直接引用事实经 `bind_references` 逐参数绑定，
不把单变量变换表达式冒充对象；形参重绑定与对象字段修改分开。`argument_field_bindings`
保存对象形参字段入口，接收者沿用既有映射。初始化与已支持单类型工厂结果共用权限回调，
字段接口仅按函数/对象根缓存，不缓存整份临时图。`post_calls.py` 统一正常出口观察与调用后定义，
构造器和原具名输出缓冲区也复用；异常边绕过后置写入。字段经精确节点 ID 的公共值边界返回，
并核对原调用上下文，不生成新结构调用边。旧图未声明确定写入能力时保持 may，不能因缺失信息清除旧值。

ProgramGraph 1.12 在同一引用环境增加字段→对象关系，复用 AST 直接引用 RHS 与 AccessPath，逐跳解析嵌套组合的末级字段；不把路径拼接当父对象字段。构造器纯引用存储摘要复用同一引用算法，标量副作用继续走原正常出口与调用后写回。组合来源与构造器真实赋值进入既有证据，默认 16 段路径及原状态预算有界；不增加语言私有图或污点引擎。

支持有限直接/局部实例返回、不同类型分支返回及四层工厂转发。Python、Java、JS/TS 支持已知局部实例的直接引用别名、有限多层组合读取/标量写入，以及唯一目标/实际类型下的字段参数写回与接收者修改。1.13 再引入公共类型层次与操作级存储事实，详见下段。工厂体内修改后返回、工厂别名返回、组合子对象的工厂返回导出、跨过程组合引用改变、任意容器别名、同对象多形参、任意堆副作用与并发仍有明确限制。当前不求解分支条件，也不是完整 points-to/堆分析。所有生成路径仍为 `may_reach_sink`，不能把候选数当作漏洞数。

`semantic_index` 1.5 的 `TypeFact/BaseTypeFact/TypeHierarchy` 唯一提供继承/实现/嵌入、C3 与 C++ 基类子对象查询，依赖解析和安全对象消费者共用。`object_types.py` 只筛选已有具体实现，`constructor_effects.py` 复用正常出口摘要编译已解析父初始化。`frontends/storage.py` 只提取 C/C++/Go 的值分配、取址/解引用与复制角色；`object_storage.py` 在私有视图物化已知值布局，`ObjectReferenceIndex` 复用同一环境处理引用和复制，原 RD/DFG 继续跟踪值。值参数不写回原对象，指针/C++ 引用/Go 指针接收者复用正常出口写回；未知副作用后失效引用并保留前沿。规则声明的输出写入先生成再统一绑定，避免 HTTP 绑定内容、返回状态码与字段覆盖相互混淆。任意指针、完整 C++ 布局与 Go 提升等局限见 flow_analysis README；不以公共协议存在宣称所有语言语义覆盖。

`SecurityEvidencePack 2.7` 使用 `entrypoints`、`facts`、`call_edges`、`dataflows`、`snippets` 顶层注册表，候选只保存 ID。`FlowStepEvidence.object_type_symbol` 与分配、调用点、原始源码位置一起传入详情及导航。原图不就地修改，旧产物可读取，但须重新分析才能获得新链路。

ProgramGraph 1.14 的 ObjectSnapshot 只导出返回可达对象和共享关系，不是整仓库堆图；工厂和引用写回
通过 object_snapshots 共用重映射，ObjectReferenceIndex 仍是唯一引用环境。ObjectCallCompiler
将权限与正常出口效果归同一实现：局部写回、工厂导入、新可绑定调用逐轮衔接，不把生成的退出观察点
反当源码操作。引用来源可为已知参数/子路径或被调函数局部新对象，后者的字段值沿既有值边界传输；
实际外部对象不克隆。引用改变保留原始赋值位置和已有调用边，可进入按 ID 的详情，不把生成节点当源码。
旧的“按返回变量名找构造器”实现已移除；复杂引用/深层值混合副作用及容器等仍有前沿，详见 flow_analysis README。

C/C++ 库 API 使用头文件与词法名称过滤，未执行完整预处理或编译器类型绑定；LLM 端点匹配
置信度为 `medium`，相关局限进入候选和证据头。`system/popen` 是隐式 Shell，`exec` 只匹配
可执行文件路径，`printf` 系列只匹配格式参数，`sqlite3_exec` 只匹配第二个 SQL 参数。
缓冲区 Source 显式保存输出实参位置与未建模诊断，不能以读取数量/状态码替代内容传播。

安全功能域的依赖方向保持 `projects → project_analysis → security_analysis`；安全模块复用
`program_graph` 的 CFG/DFG 和 `semantic_index` 的已解析目标。`syntax_analysis` 保存纯语法共性，
包括 C/C++ 声明符读取。数据库、产物管理、项目删除、模型请求不进入安全前端或规则包。

### 6.3 证据交付与 LLM 边界

旧 `SecurityEvidencePromptBuilder/LLMSecurityEvidenceEnvelope` 生成路径已经删除，不再作为当前架构。初始输入统一为 `SecurityEvidenceSelector` 生成的 `security-path-summary-v2`：传播路径优先，其次结构线索、操作线索；完整证据留在服务器，由 `SecurityEvidenceCatalog.detail(id, section, offset, limit)` 读取。实际显示范围、批次状态、发现台账和最终报告属于 Agent/实验域，不属于静态求解器。

普通 Agent 与分批实验有不同工具门面，但复用同一个 Catalog，不是两套证据生成/查询实现。源码片段目前是定位附近的有界片段，不宣称 CFG/DFG 反向切片已经完成。`knowledge.py` 仅预留 CWE 等异步知识检索接口，未启用 RAG；外部知识不能变成项目源码事实。当前优化评估与实施顺序见 `MODULE_REFACTORING.md` 第 9 节。

后续外部安全模型不是源码解析的替代品：项目/编译器负责确认调用的库、声明和实际参数，
结构化、版本化的外部模型提供安全角色、敏感槽位、传播/副作用与适用条件。漏洞公告库不能
单独替代这些模型，解释性 RAG 文档也不直接驱动污点传播。当前有限框架模型继续离线可用，
不因以后接入外部来源而删除非原生分析或把未命中模型视为安全。本阶段没有接入外部数据库。

语义索引 1.3 新增普通变量引用与源码声明实体（Go/TS 可选原生能力），身份由 program_index
统一生成；包含写引用，不代表运行时内存、赋值版本、使用点流敏感类型或值流边。调用声明、
变量引用绑定、CFG/到达定义/SSA 和安全规则仍各自拥有职责，不能因绑定唯一就跳过传播分析。

### 6.1 项目级编译器语义

JS/TS 的调用目标优先由锁定 TypeScript 6.0.3 的只读 Node 工具提供声明绑定，接口为
`semantic_index.resolution.ProjectSemanticResolver`。`SemanticIndexProvider` 仍只做产物转换，
不混入工具运行职责。依赖分析在写调用边前通过 `CompilerBindingIndex` 映射既有符号；
成功时跳过旧目标推测，未覆盖时保留 fallback_reason，虚分派仍只是 may 候选。
依赖图、公共索引与安全分析共享这次选择，不分别求一套目标。不替换现有 CFG/DFG、规则、
证据选择或模型协议；Vue 本批仍回退。运行版本、实现哈希、配置/诊断计数进入 backend_runs。
工作进程内部以共享声明/类型表传输，不把同一段声明逐调用重复输出。部署和只读边界见
`backend/tools/typescript_semantics/README.md`。

任务级性能基础也位于公共语义层：DeclarationSymbolIndex 唯一声明查询、可选阶段计时/命中
元数据，以及不跳过模型约束的有界批量事实校验。Node 前端只缓存本次快照内的读取、目录、
配置、声明与坐标，不跨 Program 共享可变树，不改变 Source/Sink、候选、may 或回退含义。
这些机制不是另一套求解器；Go/Java 后端接入可复用同一接口和装配逻辑。

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

- `ProjectManifest`、`Entrypoint`、`Evidence`：项目基本信息，须区分观察、推断与生成建议，不能全部视为源码事实。
- `DependencyGraphDTO`：版本化依赖图交换格式。
- `SecurityEvidencePack 2.7`：去重静态事实、结构路径、函数内/跨过程 Def-Use、类型化对象字段、片段、覆盖率和不确定性。
- `ProgramGraphArtifact 1.17`：按函数分区的 CFG、到达定义和值流内部协议，包含集合分配、选择器事实、字段/元素/摘要联合槽及显式弱写；内部引用状态不直接公开给前端。
- `security-path-summary-v2` 与 Catalog 详情页：有界初始路径目录及按 ID 读取的持久化证据，不是完整依赖图输入。
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
