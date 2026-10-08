# 后端服务层

`services` 承担项目导入、静态分析、派生数据、持久化与清理等应用逻辑。API 层仅调用这里的稳定入口；底层分析器和工作区实现不应被路由直接拼装。

| 文件或目录 | 入口与职责 |
| --- | --- |
| `projects/` | 项目导入、CRUD、Artifact、库存/快照和可恢复删除的统一功能域。 |
| `project_analysis/` | `ProjectAnalysisPipeline.analyze(project_root)`：无持久化副作用的纯源码分析流水线。 |
| `project_workspace/` | `ProjectWorkspaceService.prepare()/publish()`：安全获取 Git/ZIP 并发布工作区。 |
| `dependency_analyzer/` | `UnifiedCodeAnalyzer`：多语言依赖图生成。 |
| `syntax_analysis/` | 依赖图与 ProgramGraph 共享的语言目录、Tree-sitter Parser 池和节点读取工具。 |
| `program_index/` | `ProgramIdentity`：依赖图、安全 IR 和未来数据流共享的文件、符号、位置与调用点身份。 |
| `graph_core/` | `GraphArtifactView`：图能力、最小节点/边投影、只读适配器和与存储无关的结构校验。 |
| `semantic_index/` | `SemanticIndexView`：持久化并复用可调用对象、调用点、解析目标和变量类型事实。 |
| `program_graph/` | `ProgramGraphService.analyze()`：全部八种依赖分析语言的公共控制 IR、函数级 CFG 和到达定义 Overlay；由静态安全分析按规则覆盖语言消费。 |
| `code_intelligence/` | `ProjectManifestBuilder.build()`、`build_repo_map()`：结构化项目事实和仓库地图。 |
| `security_analysis/` | `SecurityAnalysisService.analyze()`：通过语言前端 IR、参数角色规则和内部调用图生成入口点、Source、Sink、结构路径、最小源码片段及不确定性证据。 |
| `reports/` | 确定性中文概览报告。 |
| `project_overview.py` | `generate_project_overview(manifest, repo_map, use_model)`：静态报告或大模型概览。 |
| `analyzer.py` | `build_file_tree_with_symbols()`：把符号附加到文件树。 |

项目导入的依赖方向是 `projects → project_workspace + project_analysis`；纯分析内部再调用
`dependency_analyzer → code_intelligence/security_analysis`。静态分析模块不依赖项目数据库、用户
身份或 HTTP。失败补偿由 `projects.ProjectImportTransaction` 管理；删除由隔离区、删除 Journal 和
`ProjectDeletionJanitor` 保证可恢复。`security_analysis` 只为已注册规则的语言调用 `program_graph`。
