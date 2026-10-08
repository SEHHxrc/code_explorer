# projects 项目功能域

本模块拥有项目聚合的应用生命周期：导入、元数据、分析产物、库存/快照以及一致删除。它不实现
语言解析规则；纯源码分析由 `project_analysis.ProjectAnalysisPipeline` 完成，不可信文件操作由
`project_workspace` 完成。

## 公开入口

| 文件/类 | 职责 |
| --- | --- |
| `repository.py` / `ProjectRepository` | 只管理 `ProjectModel` 的新增、按所有权查询、白名单更新和删除。 |
| `repository.py` / `ProjectDeletionRepository` | 在单个数据库事务中检查活动任务并删除跨功能域关联记录。 |
| `artifacts.py` / `ProjectArtifactRepository` | 原子创建/替换、读取、统计、隔离、恢复和删除分析产物。 |
| `import_service.py` / `ProjectImportService` | 编排工作区获取、纯分析、发布、Artifact 和项目记录。 |
| `query_service.py` / `ProjectQueryService` | 项目库存、存储占用、资源完整性和前端快照恢复。 |
| `deletion_service.py` / `ProjectDeletionService` | 活动任务保护、资源隔离、数据库提交、补偿恢复和最终清理。 |
| `transaction.py` / `ProjectImportTransaction` | 项目导入失败时逆序补偿工作区和 Artifact。 |
| `deletion_journal.py` | 持久化跨文件系统和数据库的删除恢复状态。 |
| `deletion_janitor.py` | 启动时按数据库事实恢复或清理中断的删除操作。 |
| `contracts.py` / `ProjectUpdate` | 只允许更新明确列出的可变字段；身份、所有权、来源和受控路径不可修改。 |

`ProjectRepository.create()` 只执行新增，不是 upsert。`update_owned()` 必须同时匹配项目 ID 与
用户 ID，并只接受 `ProjectUpdate`。普通项目仓储不读取或删除 Agent、Execution、Experiment
表；跨域级联只存在于专用删除事务仓储。

## 调用方向

```text
API
└─ projects.ProjectImportService
   ├─ project_workspace.ProjectWorkspaceService
   ├─ project_analysis.ProjectAnalysisPipeline
   ├─ projects.ProjectArtifactRepository
   └─ projects.ProjectRepository
```

删除时先把工作区和 Artifact 原子移动到隔离区，再提交数据库删除。提交前失败会恢复隔离资源；
数据库已经提交后只允许继续清理，不能恢复成没有数据库记录的项目。进程中断留下的 Journal 由
`ProjectDeletionJanitor` 在下次启动时处理。
