# -*- coding: utf-8 -*-
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def setup_exception_handler(app: FastAPI) -> None:
    """向输入的 FastAPI 应用注册全局异常中间件，无返回值。

    目标：避免后端报错直接抛给前端，确保返回结构对前端透明、最小化信息量。
    """

    @app.middleware("http")
    async def global_exception_middleware(request: Request, call_next):
        """输入请求和下游调用器，输出正常响应或脱敏后的统一 500 JSON。"""
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            # 仅在后端服务器控制台打印完整日志，用于排查
            print(f"[CRITICAL ERROR TRACE]:\n{traceback.format_exc()}")

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
