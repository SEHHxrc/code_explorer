import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


def setup_exception_handler(app: FastAPI) -> None:
    """向输入的 FastAPI 应用注册全局异常中间件，无返回值。

    目标：避免后端报错直接抛给前端，确保返回结构对前端透明、最小化信息量。
    """

    @app.middleware("http")
    async def global_exception_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """输入请求和下游调用器，输出正常响应或脱敏后的统一 500 JSON。"""
        try:
            return await call_next(request)
        except Exception:
            logger.exception("http.unhandled_error")

            # 返回格式化、脱敏后的最小化错误信息给前端
            return JSONResponse(
                status_code=500,
                content={
                    "code": 50000,
                    "message": (
                        "Internal Server Error: Operation failed securely."
                    ),
                    "data": None,
                },
            )
