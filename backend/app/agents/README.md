# 项目智能体模块

该模块把“用户问题 + 项目静态分析产物 + 受限只读工具”组织成可持久化、可取消、可通过 SSE 恢复的智能体运行。模型可在线或离线部署；没有配置模型或请求关闭模型时，会返回静态上下文结果。

## 对外入口

HTTP 入口是 `api/agent.py:create_agent_run()`：请求体使用 `AgentRunRequest`。

| 参数 | 类型与限制 | 作用 |
| --- | --- | --- |
| `question` | `str`，1～8000 字符 | 用户对当前项目的问题。 |
| `use_model` | `bool`，默认 `true` | 是否调用已配置的大模型；为 `false` 时走静态结果。 |
| `max_steps` | `int`，1～6，默认 4 | 模型—工具循环的最大轮数。 |

真正的运行入口是 `AgentRunManager.start(run_id, project_id, user_id, project_root, artifact, request)`。队列 Worker 使用 `AgentRunStore.claim_next()` 原子认领后调用它；开发环境也可由 FastAPI 生命周期启动内嵌 Worker。

## 运行时序

```text
POST /api/agent/projects/{project_id}/runs
  → AgentRunStore.create() 持久化 run 与 queue job
  → AgentQueueWorker.claim_next() 认领并续租
  → ProjectContextBuilder.build() 生成 manifest + 相关 repo map
  → create_model_provider() 选择在线或本地模型
  → AgentRunManager.generate_with_tools()
      ├─ 无 tool_calls：保存答案并完成
      └─ 有 tool_calls：ToolRegistry.execute()
           → Pydantic 严格校验参数
           → 只读工具返回内容、证据和截断标记
           → 作为 TOOL_OBSERVATIONS 进入下一轮
  → 达到步数上限时 generate() 汇总
  → 持久化 run.completed/run.failed 等事件
  → SSE 以事件游标返回前端
```

## 文件与核心类

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `AgentRunRequest`、`AgentClaim`、`ContextPacket`、`ToolResult`、`AgentEvent`、`AgentRunView` 等边界模型。 |
| `context_builder.py` / `ProjectContextBuilder` | 验证 Manifest，按问题关键词筛选仓库地图，并限制提示上下文长度。 |
| `orchestrator.py` / `AgentRunManager` | 执行模型—工具循环、收集证据、输出事件、处理取消与错误。 |
| `run_store.py` / `AgentRunStore` | 保存运行、队列租约、事件和取消状态。 |
| `worker.py` / `AgentQueueWorker` | 认领队列、续租并调用编排器；`python -m backend.app.agents.worker` 是独立进程入口。 |
| `policy.py` | 安全解析项目内相对路径，并对读取文本做敏感信息脱敏。 |
| `tools/` | 模型可请求的只读工具和严格参数模型。 |

## 上下文来源与依赖

`ProjectContextBuilder` 读取分析产物中的 `manifest` 和 `repo_map`；工具还会读取依赖图和经清洗后的项目目录。`AgentRunManager` 依赖 `llm.create_model_provider()`、`ToolRegistry` 和 `AgentRunStore`。普通 Agent 始终包含依赖图上下文；无图逻辑仅存在于带有“临时对照组”标记的实验包中。

## 安全保证

系统提示明确把仓库内容、README 和工具输出视为不可信数据。模型不能选择任意 Python 函数或宿主命令，只能按注册表调用工具。工具是只读的，并受路径边界、字节数、行数、文件数、耗时和结果数限制；错误通过公开消息返回，不暴露内部路径或密钥。
