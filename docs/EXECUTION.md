# 隔离执行域

## 进程边界

FastAPI 进程验证项目所有权和执行策略后，只把任务写入 SQLite；它不会调用 Docker，也不会执行宿主机 Shell。独立 Worker 负责认领排队任务，是唯一需要访问 Docker 守护进程的组件。

当前原型假设 API、Worker、数据库和受控项目存储位于同一主机，默认只运行一个 Worker。多主机或高并发部署前，应把队列迁移到事务型服务端数据库或专用消息代理，不要把 SQLite 放在共享网络盘上。

## 安全属性

每个容器都按以下约束启动：

- 只允许使用已预拉取的精确白名单镜像，并设置为禁止拉取；
- 禁用网络；
- 将项目工作区只读挂载到 `/workspace`；
- 容器根文件系统只读，只提供有界临时目录 `/tmp`；
- 使用数值型非 root 用户 `65532`；
- 丢弃全部 Linux capabilities，并启用 `no-new-privileges`；
- 限制 CPU、内存、PID、运行时间和捕获输出；
- 使用生成的容器名，不进行宿主机 Shell 插值。

被分析容器不会获得 Docker Socket。Docker 隔离可以降低风险，但不等同于虚拟机安全边界；生产部署还应隔离 Worker 主机、配置守护进程级 seccomp/AppArmor，并监控 Docker 守护进程。

## 配置

所有镜像配置为空时，执行功能保持禁用并失败关闭。

- `EXECUTION_ALLOWED_IMAGES`：用户命令可选择的镜像精确白名单，使用逗号分隔。
- `EXECUTION_SCAN_IMAGE_BANDIT`：预装 `bandit` 的离线扫描镜像。
- `EXECUTION_SCAN_IMAGE_SEMGREP`：预装 `semgrep` 且在 `/rules` 提供离线规则的扫描镜像。

所有镜像都必须预先存在于 Worker 主机。资源变量和单用户活动任务上限见 `backend/.env.example`。

## 启动 Worker

正常启动 FastAPI 后，在同一项目目录和 Python 环境中启动 Worker：

```powershell
.\.venv\Scripts\python.exe -m backend.app.execution.worker
```

用于健康检查或由调度器触发时，可最多处理一个排队任务：

```powershell
.\.venv\Scripts\python.exe -m backend.app.execution.worker --once
```

长期运行的 Worker 退出后应由进程管理器重新启动。Worker 会为活动任务更新心跳；重启后的 Worker 会把租约过期任务标记为失败，而不会重放用户命令。

## API 与审计

执行 API 提供配置查询、提交、项目近期任务列表、单任务查询、取消和可续传 SSE。审计记录包含策略限制、状态转换、Worker 标识、有界输出块、退出码和输出截断状态。

排队或运行中的执行任务会阻止项目删除。任务进入终态后，删除项目会在清理其他项目记录的同一数据库事务中删除执行事件和任务。
