# HTTP API 模块

本目录只处理协议层职责：解析 HTTP 输入、注入用户上下文、调用应用服务、把领域错误映射为稳定响应，并通过 SSE 输出持久化事件。业务流程不应写入路由函数。

## 文件与入口

| 文件 | 路由前缀或作用 | 主要入口 |
| --- | --- | --- |
| `project.py` | `/api/projects` | `analyze_project()`、`get_model_status()`、`create_project_overview()`、`clear_project()` |
| `agent.py` | `/api/agent` | `create_agent_run()`、`get_agent_run()`、`stream_agent_events()`、`cancel_agent_run()` |
| `experiment.py` | `/api/experiments` | `create_comparison()`、`comparison_events()`、`review_comparison()`、`reveal_comparison()` |
| `execution.py` | `/api/executions` | `get_execution_configuration()`、`create_execution_task()`、任务查询、取消与事件流 |
| `sse.py` | 公共 SSE 支撑 | `sse_response(stream)` 和 `persisted_events(...)` |

## 依赖关系

路由通过 `core.deps.get_current_user()` 读取并校验 `X-User-Id`。项目路由调用 `services.project_analysis` 和 `services.project_lifecycle`；智能体路由调用 `agents`；实验路由调用 `experiments`；执行路由调用 `execution`。所有 SSE 都复用 `sse.py`，并以持久化序号支持断线续传。

公开响应中的依赖图格式由服务层的 `graph_exchange.py` 产生。中间件只负责通用安全规则，不改写领域数据或 SSE 正文。
