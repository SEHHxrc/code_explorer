# HTTP API 模块

本目录只处理协议层职责：解析 HTTP 输入、注入用户上下文、调用应用服务、把领域错误映射为稳定响应，并通过 SSE 输出持久化事件。业务流程不应写入路由函数。

## 文件与入口

| 文件 | 路由前缀或作用 | 主要入口 |
| --- | --- | --- |
| `project.py` | `/api/projects` | 项目库存与快照恢复、导入、模型诊断、项目概览和删除 |
| `agent.py` | `/api/agent` | 创建/取消运行、按项目列出历史、恢复运行快照以及 SSE 事件流。 |
| `experiment.py` | `/api/experiments` | `create_comparison()`、`comparison_events()`、`review_comparison()`、`reveal_comparison()` |
| `execution.py` | `/api/executions` | `get_execution_configuration()`、`create_execution_task()`、任务查询、取消与事件流 |
| `sse.py` | 公共 SSE 支撑 | `sse_response(stream)` 和 `persisted_events(...)` |

## 依赖关系

路由通过 `core.deps.get_current_user()` 读取并校验 `X-User-Id`。项目路由调用 `services.project_analysis` 和 `services.project_lifecycle`；智能体路由调用 `agents`；实验路由调用 `experiments`；执行路由调用 `execution`。所有 SSE 都复用 `sse.py`，并以持久化序号支持断线续传。

数据管理使用 `GET /api/projects` 返回当前用户项目、资源完整性和存储占用；`GET /api/projects/{project_id}/snapshot` 从数据库文件树和分析产物恢复前端交换数据。删除仍调用 `DELETE /api/projects/clear/{project_id}`，活动任务存在时返回 409，避免清理运行中的工作区。

项目静态快照与 Agent 历史保持独立：`GET /api/agent/projects/{project_id}/runs` 返回普通运行摘要，`GET /api/agent/runs/{run_id}/snapshot` 返回问题、最终回答、裁剪后的展示事件和去重证据。历史列表不混入 A/B 实验的 graph/baseline 运行；快照不返回 `model.delta` 和工具结果正文，避免重复答案和不必要的源码传输。

公开响应中的依赖图格式由服务层的 `graph_exchange.py` 产生。中间件只负责通用安全规则，不改写领域数据或 SSE 正文。

模型相关接口中，`GET /model/status` 只检查配置且不访问外网；`POST /model/probe` 可接收可选的 `model`，按用户显式操作对该模型发起一次可能产生少量费用的最小生成请求，并按用户设置 10 秒单进程冷却；`GET /model/models` 调用兼容的模型目录接口。探测结果仅返回错误类型、错误代码、重试提示和请求 ID，不返回 API Key 或完整上游响应。多进程生产部署仍应在网关或共享存储中增加统一速率限制。
