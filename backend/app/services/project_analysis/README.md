# 项目分析应用模块

本包拥有“分析一个项目”的完整用例，而不是 HTTP 细节。稳定入口是：

```python
result = await ProjectAnalysisService().analyze(
    AnalyzeProjectCommand(user_id=user_id, source=WorkspaceSource.git(repo_url), max_workers=4)
)
```

ZIP 来源使用 `WorkspaceSource.zip(file_obj, filename)`。`analyze()` 输出 `ProjectAnalysisResult`，包含项目 ID、清洗报告、文件树、规范化依赖图、Manifest 和确定性概览。

## 处理阶段

```text
begin 工作区操作
→ Git/ZIP acquire
→ sanitize
→ UnifiedCodeAnalyzer.run_full_analysis()
→ 文件树 + ProjectManifest + Repo Map
→ publish 工作区
→ 保存项目记录
→ 保存无损多重图、统计和未解析诊断
→ GraphExchangeNormalizer.normalize() 聚合平行边并生成公开 DTO
→ commit
```

任一阶段失败时，`ProjectAnalysisTransaction` 按登记的相反顺序补偿数据库记录、分析产物和工作区；若补偿本身失败，会在操作日志中标记以供 Janitor 恢复。

## 文件与核心类

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `AnalyzeProjectCommand` 和 `ProjectAnalysisResult`。 |
| `service.py` / `ProjectAnalysisService` | 编排获取、清洗、分析、派生、发布与持久化。 |
| `transaction.py` / `ProjectAnalysisTransaction` | 管理补偿动作、提交和回滚结果。 |
| `graph_exchange.py` / `GraphExchangeNormalizer` | 校验数量、路径、节点和边，把原始图转换为版本化 `DependencyGraphDTO`。 |
| `repository.py` / `ProjectRepository` | 项目记录和关联运行的数据库事务边界。 |
| `artifact_repository.py` / `AnalysisArtifactRepository` | 适配 `artifact_store` 的 JSON 产物保存和删除。 |
| `exceptions.py` | 按导入、分析、项目持久化和产物持久化区分稳定公开错误。 |
| `__init__.py` | 导出服务、命令、结果和公开异常。 |

存储产物有意保留分析器的原始多重依赖图、`analysis_statistics`、`analysis_diagnostics` 和 `analysis_metadata`，供静态安全路径与证据构建使用；只有 HTTP 响应使用聚合后的交换图。数据格式化属于本应用边界，不由通用 Middleware 隐式改写。
