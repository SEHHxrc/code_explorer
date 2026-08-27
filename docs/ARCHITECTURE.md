# Code Explorer 架构与代码接口说明

本文描述当前代码，而不是未来设想。GitNexus 仅是依赖图设计的参考项目，不属于本仓库，也不是运行时依赖。

## 1. 系统边界

```text
Vue 3 / Vite
  ├─ ProjectInsight：项目导入、分析结果与概览
  ├─ DependencyGraph：Sigma.js 图展示与布局
  └─ AgentWorkspace：指令、事件流、工具步骤和结果
            │ HTTP / SSE
            ▼
FastAPI
  ├─ project API ─► Loader/Sanitizer ─► UnifiedCodeAnalyzer
  │                                  ├► ProjectManifestBuilder
  │                                  ├► RepoMapBuilder
  │                                  └► Artifact Store / SQLite
  └─ agent API ─► AgentRunManager ─► ModelProvider
                                      ├► ContextBuilder
                                      ├► Path/secret policy
                                      └► read-only ToolRegistry
```

后端是单个 FastAPI 进程。智能体任务当前由该进程内的 `asyncio.Task` 执行，运行和事件持久化在 SQLite 中；这意味着服务重启后可读取历史事件，但不会自动恢复尚未完成的任务。

## 2. 目录职责

| 路径 | 职责 | 主要输出 |
| --- | --- | --- |
| `backend/app/api` | HTTP/SSE 路由和请求边界 | JSON、SSE |
| `backend/app/services` | 仓库加载、清理、静态分析、清单、仓库地图与概览 | 分析产物 |
| `backend/app/agents` | 智能体上下文、策略、编排、事件存储和工具 | 运行事件、最终答案 |
| `backend/app/llm` | 模型配置、统一供应商接口及在线/离线实现 | 模型结果与工具调用 |
| `backend/app/schemas` | 跨模块传递的 Pydantic 数据契约 | manifest/overview DTO |
| `backend/app/models.py` | SQLAlchemy 持久化模型 | project/run/event 表 |
| `frontend/src/components` | 项目视图、依赖图、智能体界面 | Vue UI |
| `frontend/src/services` | 浏览器端 API/SSE 客户端 | 类型化调用结果 |
| `test` | 后端单元与集成测试 | 回归验证 |

`backend/storage/artifacts/<project_id>.json` 汇总保存 manifest、repo map、概览、符号和依赖图；SQLite 保存项目元数据以及智能体运行和事件。

## 3. 项目分析数据流

1. `add_project` 接收文件、本地路径或 Git 地址，并交给 Loader。
2. Loader 校验来源、限制仓库大小与路径，将内容放入受控目录。
3. `ProjectCleaner` 过滤隐藏目录、构建产物、二进制文件和超限文件。
4. `UnifiedCodeAnalyzer.run_full_analysis` 并发解析源文件：第一阶段收集定义、引用和导入，第二阶段建立索引并解析跨文件关系。
5. 分析器输出依赖图、文件符号和统计；`build_file_tree_with_symbols` 再组装前端文件树。
6. `ProjectManifestBuilder.build` 从确定性分析结果推断框架、入口点和关键文件。
7. `build_repo_map` 将清单和符号压缩成适合模型上下文的文本。
8. Artifact Store 原子写入产物；API 将轻量结果返回前端。

manifest 和 repo map 是模型的事实底座。模型用于解释和归纳，不应替代静态分析器制造不存在的文件、符号或入口点。

## 4. 智能体数据流

1. `POST /api/projects/{id}/agent/runs` 校验指令并创建运行记录。
2. `ProjectContextBuilder.build` 读取 manifest/repo map，按字符预算形成系统上下文。
3. `ToolRegistry` 只暴露固定的只读工具，`AgentRunRequest.max_steps` 将模型—工具循环限制在 1 至 6 步。
4. `AgentRunManager` 调用支持工具调用的 `ModelProvider`。
5. 模型请求、工具开始、工具结果、最终答案或错误都写为递增序号的事件。
6. 前端通过 SSE 获取增量事件；断线后用 `after=<sequence>` 继续。
7. 取消请求设置状态并取消进程内任务；终态为 `completed`、`failed` 或 `cancelled`。

目前工具只读取已分析项目。Docker 部署、安全扫描、Shell/脚本执行尚未实现，也不应直接放进 Web 进程；建议后续拆为队列驱动的执行控制面和无特权容器工作节点。

## 5. 数据与契约类

### 数据库模型

- `ProjectModel`：一个已导入项目。输入字段包括项目 ID、用户、仓库/本地地址和文件树；查询输出为项目元数据。
- `AgentRunModel`：一次智能体运行。输入为项目 ID、指令、策略和模型配置；输出包含状态、最终答案、错误及时间戳。
- `AgentEventModel`：一条有序事件。输入为运行 ID、序号、事件类型和 JSON 载荷；输出供轮询和 SSE 回放。

### 项目清单

- `Evidence`：推断结论的证据；输入路径、可选行号和说明，输出可序列化证据项。
- `Entrypoint`：程序入口；输入类型、名称、路径、可选行号/命令/框架和置信度，输出入口点描述。
- `ProjectManifest`：项目事实摘要；输入语言、框架、包管理器、入口、命令、模块、图统计和证据，输出 JSON 产物及模型上下文。
- `ProjectOverviewRequest`：概览请求；输入模型使用开关和输出语言。

### 模型抽象

- `ModelConfiguration`：统一模型设置；输入来自环境变量或请求覆盖，输出供应商实例配置。
- `ProviderCapabilities`：声明供应商是否支持工具调用等能力。
- `ModelResult`：普通文本生成结果；输出文本及供应商/模型元数据。
- `ToolCall`：规范化模型工具调用；输出调用 ID、名称和参数。
- `ModelTurn`：一轮带工具调用的模型输出；输出文本、工具调用和原始助手消息。
- `ModelProvider`：在线/离线模型统一抽象；输入消息和工具 schema，输出普通结果或工具轮次。
- `OpenAICompatibleProvider`：调用 OpenAI 风格 `/chat/completions` API。
- `OpenAIResponsesProvider`：调用 OpenAI Responses API。
- Ollama 与 vLLM 通过各自的 OpenAI 兼容端点复用 `OpenAICompatibleProvider`。

### 智能体契约

- `AgentRunRequest`：用户问题、模型使用开关与最大步骤数。
- `AgentEvidence`：智能体引用的路径、行号、符号和说明。
- `AgentRunView`：返回前端的运行状态。
- `AgentEvent`：返回前端的事件序号、类型和载荷。
- `ContextPacket`：系统提示文本、manifest、筛选后的 repo map 和初始证据。
- `ToolResult`：工具返回内容、证据及截断状态。
- `ToolContext`：工具执行所需的项目 ID、项目根目录与共享数据。
- `AgentTool`：单个工具协议；输入 JSON 参数，输出 JSON 对象。
- `ToolRegistry`：工具注册、schema 导出、参数验证和分派中心。

## 6. 静态分析核心类

`services/dependency_analyzer/` 使用 Tree-sitter 做多语言解析，并用 NetworkX 组装最终图；
它按语言拆分 `handlers/`，按流水线阶段拆分 `phases/`，由 `analyzer.py` 提供稳定门面。

- `Definition`：文件内定义；输入名称、限定名、类型、位置和元数据，输出图节点候选。
- `Reference`：符号引用；输入源定义、目标文本、引用种类和位置，输出待解析边。
- `ImportRec`：导入记录；输入模块、导入名、别名、层级和位置，输出跨文件解析线索。
- `Frame`：遍历栈的一层作用域；输入作用域种类、名称和对应定义，输出嵌套上下文。
- `FileContext`：单文件解析状态；输入路径、源码和语言，累积定义、引用、导入及局部类型信息。
- `BaseHandler`：语言处理器基类；输入语法节点和上下文，按节点类型分派回调并输出是否继续遍历。
- `PythonHandler`：Python 定义、装饰器、导入、调用和继承提取。
- `JavaScriptHandler`：JavaScript/JSX 的函数、类、变量、导入和调用提取。
- `TypeScriptHandler`：在 JavaScript 规则上增加 TypeScript 类型和接口结构。
- `GoHandler`：Go 包、函数、方法、类型、导入和调用提取。
- `JavaHandler`：Java 包、类、接口、方法、构造器和调用提取。
- `CHandler`：C 函数、结构体、预处理包含和调用提取。
- `CppHandler`：在 C 规则上增加类、命名空间、继承和方法处理。
- `RustHandler`：Rust 模块、结构体、trait、impl、函数、use 和调用提取。
- `UnifiedCodeAnalyzer`：分析总控；输入项目根目录和并发数，输出依赖图、文件符号、语言统计与文件树。

各语言处理器的 `h_*` 方法都是 Tree-sitter 内部节点回调，统一输入为当前 `Node` 和可变 `FileContext`；输出 `False` 表示已处理子树、阻止默认递归，`None/True` 表示继续递归。它们通过修改上下文产生定义、引用和导入，不作为模块公共 API。

分析器关键阶段：

- `run_full_analysis()`：扫描构造时指定的根目录，输出完整分析字典。
- `_build_indexes()`：输入第一阶段上下文，输出限定名、简单名、模块和类层级索引。
- `_resolve_inheritance()`：输入类定义与导入索引，输出解析后的继承边。
- `_build_graph_nodes()`：输入全部定义，输出 NetworkX 节点。
- `_resolve_overrides()`：输入类层级和方法定义，输出方法重写边。
- `_resolve_references()`：输入引用记录和索引，输出导入、调用和符号关系边。
- `get_progress()`：输出当前分析进度、消息和完成状态快照。

## 7. 其他后端类

- `SafeLoader` / `ProjectLoader`：限制来源、路径、大小和协议后加载本地目录、上传文件或 Git 仓库；输出受控项目目录。
- `ProjectCleaner`：按忽略规则和大小限制筛选文件；输出可分析文件集合。
- `SensitiveDataSanitizer`：识别文件类型并对环境变量、密钥等敏感内容脱敏；输出清理后的副本。
- `FileType`：清理器使用的文件类别枚举。
- `ProjectManifestBuilder`：输入依赖图，输出确定性的项目清单。
- `OverviewGenerator`：输入项目清单、仓库地图及可选模型配置，输出项目架构与功能概览。
- `ProjectContextBuilder`：输入项目 ID、问题和分析产物，输出经过筛选的上下文包。
- `policy.py`：提供项目路径边界校验、读取上限和敏感文本遮盖规则。
- `AgentRunStore`：输入运行/事件写操作或查询条件，输出持久化运行视图和事件页。
- `AgentRunManager`：输入运行请求，输出后台执行任务，并持续产生可恢复事件。
- `ManifestTool`、`EntrypointsTool`、`SearchSymbolsTool`、`ReadFileTool`、`DependencyNeighborsTool`、`SearchProjectTextTool`：六个只读工具，分别输出清单、入口、符号、源码片段、图邻居和文本命中。

## 8. 关键函数与前端接口

- `create_app()`：配置数据库、中间件和路由，输出 FastAPI 应用。
- `add_project(...)`：输入项目来源与用户信息，输出项目 ID、文件树和分析结果。
- `build_file_tree(root)`：输入根目录，输出前端可消费的层级字典。
- `save_project_artifacts(...)`：输入 manifest/repo map，原子写文件并输出路径集合。
- `load_project_manifest(id)` / `load_repo_map(id)`：输入项目 ID，输出已存储产物。
- `build_repo_map(manifest, file_symbols)`：输入清单和符号表，输出有长度预算的文本地图。
- `get_model_configuration(overrides)`：输入可选请求覆盖，输出合并环境变量后的配置。
- `create_model_provider(config)`：输入统一配置，输出在线或离线供应商。
- `post_json(url, payload, headers, timeout)`：输入 HTTP 参数，输出解码后的 JSON 对象。
- `redact_sensitive_text(text)`：输入上下文文本，输出密钥与令牌被遮盖的文本。

前端组件：

- `ProjectInsight.vue`：页面协调器；输入用户项目来源、文件选择和模型配置，输出分析请求、项目状态与子视图属性。
- `DependencyGraph.vue`：输入 `graphData` 与 `projectId`，输出可筛选、选择、布局的 Sigma 图和节点/边事件。
- `AgentWorkspace.vue`：输入项目 ID，输出智能体运行创建/取消请求，并消费 SSE 形成步骤和答案。
- `agentApi.js`：输入项目/运行 ID 与请求体，输出运行、事件订阅和取消调用。
- `graphStyle.js`：输入节点、边和图统计，输出统一节点大小、颜色、标签及边样式。

依赖图布局先按社区与拓扑层生成种子位置，再在可见子图上运行 ForceAtlas2，最后做有限次数的节点碰撞消解。节点大小保持统一；层级、重要性和选择状态通过颜色、标签与透明度表达。

## 9. 安全与扩展边界

- 路径必须位于项目根目录内，工具不能读取任意服务器文件。
- 文件读取和搜索都有字符数、行数、命中数及扩展名限制。
- 上下文发送模型前再次脱敏；模型只能调用策略白名单中的工具。
- SSE 事件带单调序号，支持断线续传和审计。
- 在线模型会把选定上下文发送到配置的 API；离线 Ollama 可避免离开本机，但仍需信任模型服务主机。

增加 Docker 和命令执行时，应新增独立 `execution` 模块：API 只提交声明式任务，队列调度短生命周期无特权容器；镜像白名单、只读挂载、CPU/内存/PID/时间限制、默认禁网、seccomp/AppArmor、输出限额和完整审计应作为不可绕过的执行层约束。
