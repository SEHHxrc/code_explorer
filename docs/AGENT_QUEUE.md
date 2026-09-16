# 智能体持久化队列

智能体请求不再依赖最初接收请求的 FastAPI 进程。API 会在同一事务中创建一条 `agent_runs` 运行记录和一条 `agent_jobs` 队列记录。`AgentQueueWorker` 原子认领最早的排队运行、续租、调用现有的只读 `AgentRunManager`，并保持原有 SSE 事件协议不变。

## 恢复语义

- 排队运行可跨 API 重启保留，并在 Worker 恢复后被认领。
- 运行中任务持有可续期的数据库租约。
- 运行任务租约过期后标记为 `failed`，不会自动重试；重复调用在线模型可能产生重复费用。
- 取消排队任务会立即进入终态。
- 运行中取消会持久化，并由 Worker 跨进程观察，因此不依赖原 API 进程。
- 事件游标使用事件表的全局自增键，在并发取消和 Worker 写入时仍保持单调递增。
- 从旧的进程内实现升级是增量式的：新增 `agent_jobs` 表；Worker 启动时会为仍处于活动状态的旧运行补充默认队列项。

## 部署方式

原型默认把一个 Worker 嵌入 FastAPI：

```powershell
uvicorn backend.app.main:app --reload
```

如需由进程管理器单独监管 Worker，先关闭内嵌消费者，再启动独立进程：

```powershell
$env:AGENT_WORKER_EMBEDDED = "0"
uvicorn backend.app.main:app
```

```powershell
$env:AGENT_WORKER_ID = "agent-worker-1"
python -m backend.app.agents.worker
```

使用 `python -m backend.app.agents.worker --once` 可最多处理一个排队运行。同一主机上的多个 Worker 可以安全竞争 SQLite 队列项；在多主机生产部署前，应改用 PostgreSQL 或专用消息队列。

`baseline` 队列策略是明确标记的临时对照组。如果依赖图实验胜出，应将该策略分支与其余 baseline 包一并删除。
