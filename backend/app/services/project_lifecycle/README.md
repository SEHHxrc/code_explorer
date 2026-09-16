# 项目生命周期模块

本模块集中处理项目删除，避免路由分别删除数据库、分析产物和文件目录而产生半完成状态。

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` / `ProjectDeletionResult` | 返回已删除项目 ID 和删除状态。 |
| `contracts.py` / `ProjectLifecycleError` | 携带可公开消息与 HTTP 状态码。 |
| `service.py` / `ProjectLifecycleService` | `delete(project_id, user_id)` 异步入口；内部 `_delete_sync()` 执行一致性删除。 |

删除前会验证项目所有权，并拒绝删除仍有活跃 Agent 或执行任务的项目。数据库相关记录在同一事务中删除，随后清理分析产物和已发布工作区。HTTP 入口是 `api/project.py:clear_project()`。
