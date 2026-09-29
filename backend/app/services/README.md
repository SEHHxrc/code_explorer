# 后端服务层

`services` 承担项目导入、静态分析、派生数据、持久化与清理等应用逻辑。API 层仅调用这里的稳定入口；底层分析器和工作区实现不应被路由直接拼装。

| 文件或目录 | 入口与职责 |
| --- | --- |
| `project_analysis/` | `ProjectAnalysisService.analyze(command)`：完整导入—清洗—分析—派生—持久化事务。 |
| `project_workspace/` | `ProjectWorkspaceService.prepare()/publish()`：安全获取 Git/ZIP 并发布工作区。 |
| `dependency_analyzer/` | `UnifiedCodeAnalyzer`：多语言依赖图生成。 |
| `syntax_analysis/` | 依赖图与 ProgramGraph 共享的语言目录、Tree-sitter Parser 池和节点读取工具。 |
| `program_index/` | `ProgramIdentity`：依赖图、安全 IR 和未来数据流共享的文件、符号、位置与调用点身份。 |
| `graph_core/` | `GraphArtifactView`：图能力、最小节点/边投影、只读适配器和与存储无关的结构校验。 |
| `semantic_index/` | `SemanticIndexView`：持久化并复用可调用对象、调用点、解析目标和变量类型事实。 |
| `program_graph/` | `ProgramGraphService.analyze()`：全部八种依赖分析语言的公共控制 IR、函数级 CFG 和到达定义 Overlay；由静态安全分析按规则覆盖语言消费。 |
| `code_intelligence/` | `ProjectManifestBuilder.build()`、`build_repo_map()`：结构化项目事实和仓库地图。 |
| `security_analysis/` | `SecurityAnalysisService.analyze()`：通过语言前端 IR、参数角色规则和内部调用图生成入口点、Source、Sink、结构路径、最小源码片段及不确定性证据。 |
| `project_lifecycle/` | `ProjectLifecycleService.delete_project()`：检查活跃任务并事务化删除。 |
| `project_inventory.py` | `ProjectInventoryService.list()/snapshot()`：统计用户项目占用、识别缺失资源并恢复前端分析快照。 |
| `reports/` | 确定性中文概览报告。 |
| `project_overview.py` | `generate_project_overview(manifest, repo_map, use_model)`：静态报告或大模型概览。 |
| `artifact_store.py` | 分析产物 JSON 的原子保存、读取、大小查询与删除。 |
| `analyzer.py` | `build_file_tree_with_symbols()`：把符号附加到文件树。 |

项目分析的核心依赖顺序是 `project_workspace → dependency_analyzer → code_intelligence/security_analysis → artifact/project repository`。`security_analysis` 在内部按已注册规则的语言调用 `program_graph`；公共函数图不进入前端依赖图交换，也不为未覆盖语言做无效解析。失败补偿由 `project_analysis.transaction` 统一登记并逆序执行。
