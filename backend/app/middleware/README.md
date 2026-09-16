# 后端中间件模块

本目录提供与具体业务无关的响应安全保证。它不承担依赖图等领域数据的规范化；领域 DTO 应由服务层构造，以免中间件隐式改变接口语义或破坏 SSE。

| 文件 | 入口 | 作用 |
| --- | --- | --- |
| `exception_handler.py` | `setup_exception_handler(app)` | 捕获未处理异常，记录内部信息并向客户端返回脱敏错误。 |
| `response_security.py` | `setup_response_security(app)` | 增加安全响应头，对普通 JSON 响应执行大小检查；跳过流式响应正文改写。 |

二者在 `main.py` 创建 FastAPI 应用后安装。CORS 由 `main.py` 单独配置。新增中间件时应确保不会缓存、消费或重写 SSE 流，也不要在通用层猜测领域模型。
