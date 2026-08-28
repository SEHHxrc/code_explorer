# -*- coding: utf-8 -*-
"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.agents.worker import agent_queue_worker
from backend.app.api.agent import router as agent_router
from backend.app.api.experiment import router as experiment_router
from backend.app.api.execution import router as execution_router
from backend.app.api.project import router as project_router
from backend.app.middleware.exception_handler import setup_exception_handler
from backend.app.middleware.response_security import setup_response_security
from backend.app.models import init_db
from backend.app.services.project_workspace.janitor import WorkspaceJanitor

init_db()
WorkspaceJanitor().cleanup_stale()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """按配置启动嵌入式 Agent Worker；外置部署可显式关闭。"""
    embedded_worker = os.getenv("AGENT_WORKER_EMBEDDED", "1").strip().lower() not in {"0", "false", "no"}
    if embedded_worker:
        agent_queue_worker.start()
    try:
        yield
    finally:
        if embedded_worker:
            await agent_queue_worker.stop()


app = FastAPI(title="AI Code Insight Assistant", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 通用中间件只处理异常脱敏、请求限制和安全头，不改写领域响应或 SSE 正文。
setup_exception_handler(app)
setup_response_security(app)

app.include_router(project_router)
app.include_router(agent_router)
app.include_router(experiment_router)
app.include_router(execution_router)


@app.get("/")
def read_root():
    """健康检查入口；无输入，输出后端可用状态。"""
    return {"code": 200, "message": "Backend service is running securely."}
