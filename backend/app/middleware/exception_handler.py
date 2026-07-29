# -*- coding: utf-8 -*-
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def setup_exception_handler(app: FastAPI):
    """全局异常与输出格式化

    目标：避免后端报错直接抛给前端，确保返回结构对前端透明、最小化信息量。
    """

    @app.middleware("http")
    async def global_exception_middleware(request: Request, call_next):
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