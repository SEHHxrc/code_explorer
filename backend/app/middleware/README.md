# 后端中间件模块

本目录提供与具体业务无关的响应安全保证。它不承担依赖图等领域数据的规范化；领域 DTO 应由服务层构造，以免中间件隐式改变接口语义或破坏 SSE。

| 文件 | 入口 | 作用 |
| --- | --- | --- |
| `exception_handler.py` | `setup_exception_handler(app)` | 捕获未处理异常，记录内部信息并向客户端返回脱敏错误。 |
| `response_security.py` | `setup_response_security(app)` | 验证请求 Content-Length 上限、维护请求 ID 和安全响应头，记录无正文/查询参数的路由模板访问概要；不改写 SSE。 |

二者在 `main.py` 创建 FastAPI 应用后安装。CORS 由 `main.py` 单独配置。新增中间件时应确保不会缓存、消费或重写 SSE 流，也不要在通用层猜测领域模型。

异常记录使用 `core.logging` 的安全 JSON 格式，不再打印原始 traceback/异常正文。访问日志是 `http.response_started`，耗时截至响应头就绪，不冒称 SSE 已传输完成；成功 GET 为 DEBUG，错误响应仍记录。经校验的追踪 ID 可来自 X-Request-ID，但不当作身份凭证；认证头、其他请求头、原始 URL、查询、上传内容及响应正文不记录。
