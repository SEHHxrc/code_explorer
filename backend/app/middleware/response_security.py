"""通用 HTTP 边界安全策略；不解析或改写业务响应正文。"""

from __future__ import annotations

import os
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DEFAULT_MAX_REQUEST_BYTES = 220 * 1024 * 1024


def setup_response_security(app: FastAPI) -> None:
    """注册请求体上限、请求追踪和通用安全响应头。"""
    try:
        configured_limit = max(1, int(os.getenv("MAX_HTTP_REQUEST_BYTES", _DEFAULT_MAX_REQUEST_BYTES)))
    except ValueError:
        configured_limit = _DEFAULT_MAX_REQUEST_BYTES

    @app.middleware("http")
    async def response_security_middleware(request: Request, call_next):
        """验证通用请求元数据并为所有响应附加安全头；保持 SSE/JSON 正文原样。"""
        started_at = time.perf_counter()
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                exceeds_limit = int(content_length) > configured_limit
            except ValueError:
                exceeds_limit = True
            if exceeds_limit:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "code": 41300,
                        "message": "Request body exceeds the configured size limit.",
                        "data": None,
                    },
                )
                return _secure(response, request_id, started_at, request.url.path)

        response = await call_next(request)
        return _secure(response, request_id, started_at, request.url.path)


def _secure(response, request_id: str, started_at: float, path: str):
    """只追加通用响应头，不读取或重建响应体。"""
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started_at) * 1000:.2f}"
    if path.startswith("/api/"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers.setdefault("Cache-Control", "no-store")
    return response