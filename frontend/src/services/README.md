# 前端通信服务

本目录是组件访问后端的唯一入口，集中处理 API 根地址、错误消息、响应拆包和 SSE 帧解析。

| 文件 | 导出入口 | 对应后端 |
| --- | --- | --- |
| `httpClient.js` | `API_BASE`、`apiClient`、`apiErrorMessage()` | Axios 基础配置；`VITE_API_BASE_URL` 默认为 `http://localhost:8000`。 |
| `projectApi.js` | 模型状态、Git/ZIP 分析、生成概览、删除项目 | `/api/projects` |
| `agentApi.js` | `createAgentRun()`、`cancelAgentRun()`、`streamAgentEvents()` | `/api/agent` |
| `experimentApi.js` | 创建比较、提交盲评、比较 SSE | `/api/experiments` |
| `executionApi.js` | 配置、创建/查询/取消任务、执行 SSE | `/api/executions` |
| `sseClient.js` | `consumeSse(eventsUrl, onData, signal, errorLabel)` | 解析有界 `text/event-stream`，忽略 heartbeat，支持 AbortSignal。 |

业务组件应调用这些函数，不直接使用 Axios/Fetch。`consumeSse()` 当前只向回调传递 JSON `data:` 内容；续传游标由各业务 API 返回的事件 URL和后端事件序号协议管理。
