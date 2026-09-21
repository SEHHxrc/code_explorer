# 后端服务层

`services` 承担项目导入、静态分析、派生数据、持久化与清理等应用逻辑。API 层仅调用这里的稳定入口；底层分析器和工作区实现不应被路由直接拼装。

| 文件或目录 | 入口与职责 |
| --- | --- |
| `project_analysis/` | `ProjectAnalysisService.analyze(command)`：完整导入—清洗—分析—派生—持久化事务。 |
| `project_workspace/` | `ProjectWorkspaceService.prepare()/publish()`：安全获取 Git/ZIP 并发布工作区。 |
| `dependency_analyzer/` | `UnifiedCodeAnalyzer`：多语言依赖图生成。 |
| `code_intelligence/` | `ProjectManifestBuilder.build()`、`build_repo_map()`：结构化项目事实和仓库地图。 |
| `project_lifecycle/` | `ProjectLifecycleService.delete_project()`：检查活跃任务并事务化删除。 |
| `project_inventory.py` | `ProjectInventoryService.list()/snapshot()`：统计用户项目占用、识别缺失资源并恢复前端分析快照。 |
| `reports/` | 确定性中文概览报告。 |
| `project_overview.py` | `generate_project_overview(manifest, repo_map, use_model)`：静态报告或大模型概览。 |
| `artifact_store.py` | 分析产物 JSON 的原子保存、读取、大小查询与删除。 |
| `analyzer.py` | `build_file_tree_with_symbols()`：把符号附加到文件树。 |

项目分析的核心依赖顺序是 `project_workspace → dependency_analyzer → code_intelligence → artifact/project repository`。失败补偿由 `project_analysis.transaction` 统一登记并逆序执行。
