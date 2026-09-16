# Code Explorer 架构与代码接口说明

本文描述当前代码边界与实际入口。各目录的文件、类和依赖关系见对应 `README.md`。

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
├─ ProjectAnalysisService
│  ├─ ProjectWorkspaceService（Git/ZIP、清洗、发布、回滚日志）
│  ├─ UnifiedCodeAnalyzer（Tree-sitter + NetworkX）
│  ├─ ProjectManifestBuilder / build_repo_map
│  └─ ProjectRepository / AnalysisArtifactRepository
├─ AgentQueueWorker → AgentRunManager
│  ├─ ProjectContextBuilder
│  ├─ ModelProvider（OpenAI Responses 或 OpenAI 兼容 API）
│  └─ 只读 ToolRegistry
├─ ExperimentComparisonService（有图 / 临时无图对照）
└─ ExecutionService → ExecutionWorker → DockerExecutor
```

通用 Middleware 只处理异常脱敏、安全响应头和响应大小，不改写领域 DTO 或 SSE。依赖图交换格式由 `GraphExchangeNormalizer` 在服务边界显式生成。

## 2. 模块职责与文档入口

| 模块 | 职责 | 详细说明 |
| --- | --- | --- |
| 后端应用根 | FastAPI 装配、数据库初始化和 Worker 生命周期 | `backend/app/README.md` |
| HTTP API | 请求、用户上下文、错误映射和 SSE | `backend/app/api/README.md` |
| 项目分析 | 导入—清洗—分析—派生—持久化事务 | `backend/app/services/project_analysis/README.md` |
| 安全工作区 | Git/ZIP 获取、路径策略、清洗、发布和恢复 | `backend/app/services/project_workspace/README.md` |
| 依赖分析器 | 多语言语法提取与跨文件关系解析 | `backend/app/services/dependency_analyzer/README.md` |
| 大模型接入 | 配置、Provider 抽象与 HTTP 协议 | `backend/app/llm/README.md` |
| 项目智能体 | 上下文、模型—工具循环、队列与事件 | `backend/app/agents/README.md` |
| 只读工具 | JSON Schema、参数校验、源码/图查询 | `backend/app/agents/tools/README.md` |
| 对照实验 | 有图/无图盲态配对与指标 | `backend/app/experiments/README.md` |
| 隔离执行 | 策略、队列、Docker Worker 和审计 | `backend/app/execution/README.md` |
| 前端功能 | 项目、图、实验和执行页面 | `frontend/src/features/README.md` |
| 前端通信 | Axios 与 SSE 客户端 | `frontend/src/services/README.md` |

分析产物写入 `backend/storage/artifacts/<project_id>.json`，其中保留 Manifest、Repo Map、原始依赖图和符号证据。SQLite 保存项目元数据、Agent 队列/事件、实验记录和执行队列/事件。

## 3. 项目分析与事务回滚

1. `POST /api/projects/analyze` 把 Git URL 或 ZIP 转换为 `AnalyzeProjectCommand`。
2. `ProjectAnalysisTransaction.begin()` 创建受控暂存操作并登记补偿。
3. `ProjectWorkspaceService.prepare()` 获取来源并由 `ProjectSanitizer` 清理链接、敏感文件、超大文件、禁止类型和噪声目录。
4. `UnifiedCodeAnalyzer.run_full_analysis()` 按采集、索引、导入、类型和图构建五阶段产生原始依赖图。
5. `build_file_tree_with_symbols()`、`ProjectManifestBuilder.build()` 和 `build_repo_map()` 产生前端与模型共同使用的确定性事实。
6. 工作区发布后，项目记录和分析产物依次持久化。
7. `GraphExchangeNormalizer.normalize()` 生成有版本、有限额、路径安全的公开图 DTO。
8. 全部成功后提交事务；任何异常都会逆序删除已写入的产物、数据库记录和工作区。补偿失败会写入操作日志，供 `WorkspaceJanitor` 在后续启动时清理。

Manifest 与 Repo Map 是模型事实底座。模型只负责解释和归纳，不应替代静态分析器制造不存在的文件、符号或入口点。

## 4. 大模型与智能体调用链

模型配置由 `get_model_configuration()` 读取以下环境变量：

- `CODE_EXPLORER_LLM_PROVIDER`
- `CODE_EXPLORER_LLM_MODEL`
- `CODE_EXPLORER_LLM_API_KEY`
- `CODE_EXPLORER_LLM_BASE_URL`

`create_model_provider()` 对 OpenAI 创建 `OpenAIResponsesProvider`，对 Ollama、vLLM 或自定义兼容服务创建 `OpenAICompatibleProvider`。普通文本入口是 `generate(instructions, prompt)`；工具入口是 `generate_with_tools(instructions, prompt, tools)`。

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

分析器不执行项目代码。语言内置、标准库和第三方依赖使用独立节点类别，前端可按层级筛选或临时隐藏。

## 6. 隔离执行与安全扫描

执行请求由 `ExecutionTaskRequest` 描述为 `argv` 数组，不能提交宿主 Shell 字符串。`ExecutionPolicy` 先验证镜像白名单、扫描 Profile 和资源上限，`ExecutionService` 只负责入队。独立 `ExecutionWorker` 再按当前策略复核计划，最后调用 `DockerExecutor`。

容器禁网、项目只读挂载、根文件系统只读、非 root、丢弃 capabilities，并限制 CPU、内存、PID、时间和输出。执行模块不作为 Agent 工具注册，因此模型不能自行启动容器。完整配置见 `docs/EXECUTION.md`。

## 7. 前端架构

`features/project-insight/ProjectInsight.vue` 是工作台总入口，使用异步组件和 `KeepAlive` 切换概览、代码与图、Agent、实验和执行页面。项目状态由 `useProjectAnalysis()` 管理，删除或重新导入时先中止在途请求并销毁旧图实例。

依赖图把后端 DTO 转换为 Graphology 图，只对可见子图运行 ForceAtlas2 Worker 和 NoOverlap；Sigma.js 负责相机、拖拽和选择。所有节点大小统一，类别通过颜色和标签表达。右侧符号栏调用 `revealSymbol(target)` 恢复完整图并聚焦对应节点。

组件只通过 `src/services` 访问后端。Axios 处理普通 HTTP，`consumeSse()` 处理 JSON SSE 和取消信号。

## 8. 持久化模型与契约

主要数据库模型：

- `ProjectModel`：已发布项目元数据和文件树。
- `AgentRunModel`、`AgentJobModel`、`AgentEventModel`：Agent 结果、队列租约和事件。
- `ExperimentComparisonModel`、`ExperimentReviewModel`：盲态比较与评审。
- `ExecutionTaskModel`、`ExecutionEventModel`：容器任务、资源计划和审计事件。

主要公开契约：

- `ProjectManifest`、`Entrypoint`、`Evidence`：确定性项目事实。
- `DependencyGraphDTO`：版本化依赖图交换格式。
- `AgentRunRequest`、`ContextPacket`、`ToolResult`、`AgentRunView`：智能体边界。
- `ModelResult`、`ModelTurn`、`ToolCall`、`ProviderCapabilities`：模型适配边界。
- `ExecutionTaskRequest`、`ExecutionPlan`、`ExecutionTaskView`：隔离执行边界。

## 9. 安全与扩展边界

- 外部项目先进入暂存目录，路径、链接、文件类型和大小都受策略约束。
- 源码工具只能访问解析后的项目根目录；读取、搜索、耗时和输出均有限额并进行敏感信息脱敏。
- 仓库内容、README、注释、模型输出和工具输出都不具备指令权限。
- API Key 不写入公开状态；上游错误正文不直接返回客户端。
- SSE 使用持久化递增游标；Agent 和执行任务支持跨进程取消。
- Docker 仍不是虚拟机安全边界；生产环境应隔离 Worker 主机并配置守护进程级 seccomp/AppArmor。
