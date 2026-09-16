# 隔离执行与安全扫描页面

`ExecutionWorkspace.vue` 是容器任务界面：读取服务端配置、创建白名单命令或安全扫描任务、展示任务列表和有界事件输出，并支持取消。

| 文件/入口 | 作用 |
| --- | --- |
| `ExecutionWorkspace.vue` | 请求表单、资源限制、任务状态、SSE 日志和取消控制。 |
| `domain/parseArgv.js` / `parseArgv(text)` | 把带引号/转义的命令输入解析成 `argv` 数组；不生成 Shell 字符串。 |

页面通过 `services/executionApi.js` 调用 `/api/executions`。前端解析仅改善输入体验，真正的镜像白名单、参数形状与资源限制仍由后端 `ExecutionPolicy` 强制执行。容器隔离细节见 `../../../../docs/EXECUTION.md`。
