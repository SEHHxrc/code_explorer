# 安全项目工作区模块

本模块把不可信的 Git URL 或 ZIP 上传转换为受控、已清洗、可发布的项目目录，并为失败回滚和进程重启后的垃圾清理保留操作日志。

## 稳定入口

`ProjectWorkspaceService` 提供以下阶段方法：

- `begin(user_id) -> WorkspaceOperation`：创建随机操作 ID、项目 ID、暂存目录和日志。
- `prepare(operation, source) -> PreparedWorkspace`：获取 Git/ZIP，执行安全清洗但不发布。
- `publish(prepared) -> Path`：原子移动到用户的正式项目目录。
- `transition()`、`finish()`、`mark_rollback_failed()`：维护恢复日志。

通常不应单独调用这些方法，而应通过 `projects.ProjectImportService` 和
`projects.ProjectImportTransaction` 使用。

## 文件与核心类

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `WorkspaceSource`、`WorkspaceOperation`、`PreparedWorkspace` 和分类 `SanitizeReport`。 |
| `service.py` / `ProjectWorkspaceService` | 组织 begin、acquire、sanitize、publish 和操作状态。 |
| `policy.py` / `WorkspacePolicy` | 定义上传、解压、文件、路径、Git 超时和允许协议等上限。 |
| `paths.py` / `ProjectWorkspacePaths` | 校验标识并生成用户、暂存、删除隔离和正式项目路径。 |
| `filesystem.py` / `WorkspaceFilesystem` | 在已验证根目录内创建、发布、隔离、恢复和删除文件树。 |
| `sanitizer.py` / `ProjectSanitizer` | 删除链接/重解析点、敏感文件、超大文件、禁止类型和噪声目录。 |
| `journal.py` / `OperationJournal` | 原子保存工作区操作状态，供回滚和重启恢复。 |
| `janitor.py` / `WorkspaceJanitor` | 启动时回收过期暂存操作和失去数据库记录的孤儿目录。 |
| `sources/` | Git 与 ZIP 两种来源适配器。 |
| `exceptions.py` | 来源校验、获取、策略和发布错误。 |

## 路径与回滚保证

所有用户 ID、操作 ID 和项目 ID 都先校验再拼接路径；删除动作还会验证目标位于预期根目录内。
ZIP 会拒绝目录穿越、绝对路径、链接和超预算条目；Git 来源仅允许策略许可的 URL。项目导入
失败时由 `projects.ProjectImportTransaction` 补偿；项目删除先移动到 `.deleting` 隔离区，数据库
失败时恢复，进程中断时由项目删除 Journal 和 Janitor 继续处理。
