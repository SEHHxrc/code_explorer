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
| `progress.py` / `ImportProgressStore`、`ImportProgressTracker` | 按现有用户上下文隔离临时导入进度，复用分析器文件计数，记录事务完成或回滚后的终态。 |
| `query_service.py` / `ProjectQueryService` | 项目库存、存储占用、资源完整性和前端快照恢复。 |
| `deletion_service.py` / `ProjectDeletionService` | 活动任务保护、资源隔离、数据库提交、补偿恢复和最终清理。 |
| `transaction.py` / `ProjectImportTransaction` | 项目导入失败时逆序补偿工作区和 Artifact。 |
| `deletion_journal.py` | 持久化跨文件系统和数据库的删除恢复状态。 |
| `deletion_janitor.py` | 启动时按数据库事实恢复或清理中断的删除操作。 |
| `contracts.py` / `ProjectUpdate` | 只允许更新明确列出的可变字段；身份、所有权、来源和受控路径不可修改。 |

`ProjectRepository.create()` 只执行新增，不是 upsert。`update_owned()` 必须同时匹配项目 ID 与
用户 ID，并只接受 `ProjectUpdate`。普通项目仓储不读取或删除 Agent、Execution、Experiment
表；跨域级联只存在于专用删除事务仓储。

## 导入进度

前端上传前调用 `POST /api/projects/analysis-progress` 获取 `request_id`，将该 ID 随 ZIP 或
Git 表单提交到原有 `POST /api/projects/analyze`，同时每秒串行查询
`GET /api/projects/analysis-progress/{request_id}`。旧客户端不传 ID 时仍按原来的同步导入接口运行。
进度 ID 只能由所属用户认领一次，其他用户无法查询或用于导入；身份认证沿用现有用户上下文，
当前原型的 `X-User-Id` 不等同于正式的登录认证。

依赖文件计数直接读取 `UnifiedCodeAnalyzer.get_progress()`，不重新扫描项目，也不将完整图
放入进度响应。文件计数包含失败/跳过的解析尝试，不代表解析成功率；文件达到 100% 后仍需
解析关系、执行安全扫描、CFG/DFG、污点传播、生成证据和保存。无可靠总量的阶段只显示阶段与耗时，
不估算全流程百分比。导入用例在线程中、事务退出之后记录成功或失败，浏览器断连不会取消后台事务。

进度是**单 API 进程内的临时观测数据**，不写数据库，不是分析历史、持久任务队列或恢复日志。
待上传任务及终态默认保留 10 分钟；上传轮询会续期待上传任务，活动分析不因长时间运行而过期。
终态和阶段切换会释放旧分析器引用，并有全局容量与单用户活动配额。当前启动方式为单 API Worker；
如果未来部署多个 API Worker/实例，需要把该登记表改为共享存储（例如 Redis），不能直接使用进程内登记表。
进程重启会丢失进度；成功提交的项目仍可通过数据管理恢复，失败回滚与中断清理仍由原有事务和 Journal 负责。

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
