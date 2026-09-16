# Docker 隔离执行模块

本模块实现队列驱动的项目命令执行和安全扫描。FastAPI 进程只负责验证与入队；独立 Worker 是唯一能够访问 Docker 守护进程的组件。详细部署和安全配置见 `../../../docs/EXECUTION.md`。

## 入口

- HTTP：`api/execution.py` 下的 `/api/executions/*`。
- 应用服务：`ExecutionService.submit(project_id, user_id, request)`。
- Worker：`python -m backend.app.execution.worker`；增加 `--once` 时最多处理一个任务。
- 请求模型：`ExecutionTaskRequest`。

命令任务接收 `kind="command"`、白名单 `image`、不经 Shell 拼接的 `argv: list[str]` 和资源限制。扫描任务接收 `kind="security_scan"` 与 `scan_profile="bandit"|"semgrep"`，镜像和命令由服务端策略决定。

## 文件与核心类

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | 请求、不可变 `ExecutionPlan`、任务/事件视图和 `ExecutionError`。 |
| `policy.py` / `ExecutionSettings` | 从环境变量读取镜像白名单、扫描镜像、资源和输出上限。 |
| `policy.py` / `ExecutionPolicy` | 入队前解析请求，执行前再次按当前策略验证计划。 |
| `service.py` / `ExecutionService` | 检查项目所有权、并发限制，创建、查询和取消任务。 |
| `repository.py` / `ExecutionRepository` | 任务、租约、状态和事件的 SQLite 事务边界。 |
| `docker_executor.py` / `DockerExecutor` | 生成固定形状的 `docker run` 参数并捕获有界输出。 |
| `worker.py` / `ExecutionWorker` | 认领队列、续租、运行/停止容器并保存审计事件。 |
| `__init__.py` | 对外导出服务、契约和终态常量。 |

## 依赖与安全边界

模块依赖项目仓库确认所有权和项目根路径，依赖 SQLite 持久化队列，依赖 Docker CLI 执行容器。容器禁网、工作区只读挂载、根文件系统只读、使用非 root 用户、丢弃 capabilities、限制 CPU/内存/PID/时间/输出。参数始终以数组和 `shell=False` 传递；用户不能把任意宿主命令变成 Shell 字符串。
