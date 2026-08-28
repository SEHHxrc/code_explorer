"""隔离执行控制面的 HTTP、取消和可续传 SSE 接口。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.core.deps import get_current_user
from backend.app.execution import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionError,
    ExecutionService,
    ExecutionTaskRequest,
)


router = APIRouter(prefix="/api/executions", tags=["Executions"])
execution_service = ExecutionService()


def _safe_call(callback):
    try:
        return callback()
    except ExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc


@router.get("/configuration")
async def get_execution_configuration(current_user: dict = Depends(get_current_user)):
    """输出执行功能是否配置、可用扫描器和不可绕过的资源上限。"""
    return {"code": 200, "data": execution_service.configuration()}


@router.post("/projects/{project_id}/tasks", status_code=202)
async def create_execution_task(
    project_id: str,
    request: ExecutionTaskRequest,
    current_user: dict = Depends(get_current_user),
):
    """验证所有权和策略后只写入队列；Web 进程不会调用 Docker。"""
    data = _safe_call(lambda: execution_service.submit(project_id, current_user["user_id"], request))
    return {"code": 202, "message": "Execution task queued.", "data": data}


@router.get("/projects/{project_id}/tasks")
async def list_execution_tasks(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """返回项目近期任务，不包含其他用户数据。"""
    rows = _safe_call(
        lambda: execution_service.list_for_project(project_id, current_user["user_id"], limit)
    )
    return {"code": 200, "data": [row.model_dump() for row in rows]}


@router.get("/tasks/{task_id}")
async def get_execution_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """返回任务当前状态和资源计划。"""
    view = _safe_call(lambda: execution_service.get(task_id, current_user["user_id"]))
    return {"code": 200, "data": view.model_dump()}


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_execution_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """取消排队任务或请求 Worker 终止正在运行的容器。"""
    view = _safe_call(lambda: execution_service.cancel(task_id, current_user["user_id"]))
    code = 200 if view.status in TERMINAL_EXECUTION_STATUSES else 202
    return {"code": code, "data": view.model_dump()}


@router.get("/tasks/{task_id}/events")
async def stream_execution_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """按序号推送审计和有界输出事件，支持断线续传。"""
    user_id = current_user["user_id"]
    _safe_call(lambda: execution_service.get(task_id, user_id))

    async def event_stream():
        sequence = after
        last_heartbeat = time.monotonic()
        while True:
            events = execution_service.repository.events_after(task_id, sequence)
            for event in events:
                sequence = event.sequence
                payload = json.dumps(event.model_dump(), ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            view = execution_service.repository.get(task_id, user_id)
            if view is None or (view.status in TERMINAL_EXECUTION_STATUSES and not events):
                break
            if time.monotonic() - last_heartbeat > 10:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
