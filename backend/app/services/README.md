# 后端服务层

## 模块变化的兼容边界

公共入口与契约是跨目录 API，私有收集器、进程和可变内部状态不是。
`language_backends` 只装配原生工具；`semantic_index` 只保存/查询事实；依赖分析维护调用目标，
程序图维护 CFG/DFG，安全分析维护信任边界、规则和证据。不在多个目录维护平行的调用解析或污点算法。
公共访问路径位于 `value_binding.access_paths`，进度协议位于 `core.progress`，底层不再反向依赖项目编排。
安全源码适配实现归 `security_analysis.language_adapters`，公共安全 IR 仍在 ir.py；旧 frontends 只兼容导出。
语法复用归 `syntax_analysis.session`，仅任务内有界生效，不与语义索引或项目存储混合。
移动实现时用薄再导出保留已公开路径；兼容入口和非原生解析不同，后者仍是必须保留的实际能力。
契约新增字段提供兼容默认值，删除或改变含义需版本化。架构回归见 `test/test_analysis_module_boundaries.py`。

`services` 承担项目导入、静态分析、派生数据、持久化与清理等应用逻辑。API 层仅调用这里的稳定入口；底层分析器和工作区实现不应被路由直接拼装。

| 文件或目录 | 入口与职责 |
| --- | --- |
| `projects/` | 项目导入、CRUD、Artifact、库存/快照和可恢复删除的统一功能域。 |
| `project_analysis/` | `ProjectAnalysisPipeline.analyze(project_root)`：无持久化副作用的纯源码分析流水线。 |
| `project_workspace/` | `ProjectWorkspaceService.prepare()/publish()`：安全获取 Git/ZIP 并发布工作区。 |
| `dependency_analyzer/` | `UnifiedCodeAnalyzer`：多语言依赖图生成。 |
| `syntax_analysis/` | 依赖图与 ProgramGraph 共享的语言目录、Tree-sitter Parser 池和节点读取工具。 |
| `value_binding/` | `PatternResolver.resolve()`：AST/规则无关的对象与序列模式投影，供程序图和安全分析共用。 |
| `program_index/` | `ProgramIdentity`：依赖图、安全 IR 和未来数据流共享的文件、符号、位置与调用点身份。 |
| `graph_core/` | `GraphArtifactView`：图能力、最小节点/边投影、只读适配器和与存储无关的结构校验。 |
| `semantic_index/` | `SemanticIndexView`：持久化并复用可调用对象、调用点、解析目标和变量类型事实。 |
| `language_backends/` | `project_resolvers()`：原生 Go/TypeScript 工具装配、受控进程和协议解码；只输出公共语义事实。 |
| `program_graph/` | `ProgramGraphService.analyze()`：全部八种依赖分析语言的公共控制 IR、函数级 CFG 和到达定义 Overlay；由静态安全分析按规则覆盖语言消费。 |
| `code_intelligence/` | `ProjectManifestBuilder.build()`、`build_repo_map()`：结构化项目事实和仓库地图。 |
| `security_analysis/` | `SecurityAnalysisService.analyze()`：通过语言前端 IR、参数角色规则和内部调用图生成入口点、Source、Sink、结构路径、最小源码片段及不确定性证据。 |
| `reports/` | 确定性中文概览报告。 |
| `project_overview.py` | `generate_project_overview(manifest, repo_map, use_model)`：静态报告或大模型概览。 |
| `analyzer.py` | `build_file_tree_with_symbols()`：把符号附加到文件树。 |

项目导入的依赖方向是 `projects → project_workspace + project_analysis`；纯分析内部再调用
`dependency_analyzer + code_intelligence + security_analysis`；`security_analysis` 再消费公共
`program_graph` 与 `semantic_index`。静态分析模块不依赖项目数据库、用户
身份或 HTTP。失败补偿由 `projects.ProjectImportTransaction` 管理；删除由隔离区、删除 Journal 和
`ProjectDeletionJanitor` 保证可恢复。`security_analysis` 只为已注册规则的语言调用 `program_graph`。
安全语言前端、规则、参数绑定和 Source-to-Sink 证据都属于 `security_analysis`；纯语法共性属于
`syntax_analysis`，函数内 CFG/DFG 属于 `program_graph`，跨文件调用目标仍由依赖分析与语义索引
维护。当前安全规则覆盖 Python、Java、Go、C/C++、JavaScript/TypeScript；Rust 仍明确报告安全规则覆盖缺口。
