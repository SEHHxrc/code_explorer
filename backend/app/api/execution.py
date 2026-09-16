"""隔离执行控制面的 HTTP、取消和可续传 SSE 接口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.api.sse import persisted_events, sse_response
from backend.app.core.deps import get_current_user
from backend.app.execution import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionError,
    ExecutionService,
    ExecutionTaskRequest,
)


router = APIRouter(prefix="/api/executions", tags=["Executions"])
T = TypeVar("T")
execution_service = ExecutionService()


def _safe_call(callback: Callable[[], T]) -> T:
    """把领域错误转换为稳定的 HTTP 错误响应。"""
    try:
        return callback()
    except ExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc


@router.get("/configuration")
async def get_execution_configuration(current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """输出执行功能是否配置、可用扫描器和不可绕过的资源上限。"""
    return {"code": 200, "data": execution_service.configuration()}


@router.post("/projects/{project_id}/tasks", status_code=202)
async def create_execution_task(
    project_id: str,
    request: ExecutionTaskRequest,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """验证所有权和策略后只写入队列；Web 进程不会调用 Docker。"""
    data = _safe_call(lambda: execution_service.submit(project_id, current_user["user_id"], request))
    return {"code": 202, "message": "Execution task queued.", "data": data}


@router.get("/projects/{project_id}/tasks")
async def list_execution_tasks(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """返回项目近期任务，不包含其他用户数据。"""
    rows = _safe_call(
        lambda: execution_service.list_for_project(project_id, current_user["user_id"], limit)
    )
    return {"code": 200, "data": [row.model_dump() for row in rows]}


@router.get("/tasks/{task_id}")
async def get_execution_task(task_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回任务当前状态和资源计划。"""
    view = _safe_call(lambda: execution_service.get(task_id, current_user["user_id"]))
    return {"code": 200, "data": view.model_dump()}


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_execution_task(task_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """取消排队任务或请求 Worker 终止正在运行的容器。"""
    view = _safe_call(lambda: execution_service.cancel(task_id, current_user["user_id"]))
    code = 200 if view.status in TERMINAL_EXECUTION_STATUSES else 202
    return {"code": code, "data": view.model_dump()}


@router.get("/tasks/{task_id}/events")
async def stream_execution_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    current_user: dict[str, str] = Depends(get_current_user),
) -> StreamingResponse:
    """按序号推送审计和有界输出事件，支持断线续传。"""
    user_id = current_user["user_id"]
    _safe_call(lambda: execution_service.get(task_id, user_id))
    repository = execution_service.repository
    return sse_response(persisted_events(
        after=after,
        events_after=lambda sequence: repository.events_after(task_id, sequence),
        current_view=lambda: repository.get(task_id, user_id),
        terminal_statuses=TERMINAL_EXECUTION_STATUSES,
    ))
