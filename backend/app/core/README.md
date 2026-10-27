# 核心依赖模块

本目录放置跨路由复用、但不属于具体业务域的基础依赖。

| 文件 | 函数 | 输入与输出 |
| --- | --- | --- |
| `deps.py` | `get_current_user(x_user_id)` | 输入 `X-User-Id` 请求头；仅接受 1～64 位字母、数字、下划线或连字符，输出包含 `user_id` 与 `roles` 的用户上下文。 |
| `progress.py` | `AnalysisProgressReporter.phase()`、`report_progress()` | 通用阶段及计数读取接口，隔离观测异常；不依赖项目事务或分析实现。原 project_analysis.progress 仅转出同一接口。 |
| `redaction.py` | `redact_source(text, path=...)`、`redact_secrets(text)` | 源码、切片和审计遮蔽凭据字面量、已知密钥格式和私钥，保留标识符、赋值、引号及换行；明确的配置文件另遮蔽裸值。日志用后者继续遮蔽裸凭据。两者复用同一组规则，不执行输入、不提供通用 DLP 保证。 |
| `logging.py` | `configure_logging(role)`、`log_context(**fields)` | 配置安全 JSON 控制台/轮转日志，绑定请求和任务关联字段；API 和独立 Worker 使用同一入口，不接管第三方 root logger。 |

当前实现用于原型系统的用户隔离，不等同于正式认证。若接入 JWT/OAuth，应保留 `get_current_user()` 这一注入入口，使各路由无需感知认证实现变化。

## 运行日志

API 启动与 Agent/Execution Worker CLI 自动调用 `configure_logging()`；嵌入式 Worker 共用 API 的处理器。重复初始化不添加重复处理器。直接嵌入服务类的宿主也应调用该入口；未配置时应用包使用 NullHandler，避免标准 lastResort 回显原始异常。Uvicorn 访问日志由安全中间件的 `http.response_started` 替代，第三方服务器若另开访问日志也须自行关闭查询参数/认证信息输出。

默认 INFO，控制台单行 JSON，同时写入 `backend/storage/logs/<角色>-<PID>.jsonl`；默认单文件 10 MiB、5 个轮转备份。`CODE_EXPLORER_LOG_FILE_ENABLED=0` 可只输出控制台，`CODE_EXPLORER_LOG_DIR` 可指定目录。文件无法写入会明确告警并降级为控制台，不阻断业务。每个进程独立文件，旧 PID 的日志不被自动删除；部署时应配置目录访问权限，并由运维归档/清理旧进程日志，当前轮转上限不是全目录配额。

关联字段包括 request_id、project_id、run_id、task_id、worker_id 和 step。上下文跨 asyncio/to_thread 继承，静态分析线程池显式复制；退出或失败后恢复，不把一个用户的请求串到另一个请求。请求只记录路由模板、方法、状态、追踪 ID 和响应头就绪的耗时，不记录 URL 查询、认证/其他请求头或正文，不消费/改写 SSE；成功 GET 轮询和健康检查记录 DEBUG，失败仍可见。

统一格式器只保留允许的标量 extra，遮蔽常见凭据、实际配置的模型密钥和 URL；异常仅保留类型、模块文件名/函数名/行号，JSON 解析错误额外记录位置，不输出异常正文、源码行、局部变量或隐藏推理。输出失败的回退也不回显原始日志参数。以上不构成通用 DLP，新增日志仍禁止直接记录源码、脚本/命令、上传名称、模型消息或认证字段。

运行日志用于排障，不用于生成安全证据或实验评分；实验私有审计仍独立保存完整的脱敏可见请求。日志去重采用明确责任边界：HTTP 每次尝试、重试等待、任务终态分别记录，不按文本抑制真实故障；不启用带项目内容的 root DEBUG/SQL 日志。
