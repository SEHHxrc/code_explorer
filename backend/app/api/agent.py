from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import TERMINAL_STATUSES, agent_run_manager
from backend.app.agents.worker import agent_queue_worker
from backend.app.api.sse import persisted_events, sse_response
from backend.app.core.deps import get_current_user
from backend.app.services.projects import ProjectArtifactRepository, ProjectRepository

router = APIRouter(prefix="/api/agent", tags=["Agent"])
projects = ProjectRepository()
artifacts = ProjectArtifactRepository()


@router.post("/projects/{project_id}/runs")
async def create_agent_run(
    project_id: str,
    request: AgentRunRequest,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """校验项目和分析产物，将运行写入持久化队列并返回 SSE 地址。"""
    user_id = current_user["user_id"]
    if projects.get_owned(project_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized.")
    if not artifacts.load(project_id):
        raise HTTPException(status_code=409, detail="Project analysis artifact is missing.")
    run_id = uuid.uuid4().hex
    view = agent_run_manager.store.create(
        run_id=run_id,
        project_id=project_id,
        user_id=user_id,
        request=request,
    )
    agent_queue_worker.notify()
    return {
        "code": 202,
        "message": "Agent run accepted.",
        "data": {
            **view.model_dump(),
            "events_url": f"/api/agent/runs/{run_id}/events",
        },
    }


@router.get("/runs/{run_id}")
async def get_agent_run(run_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回当前用户可见的最新运行状态。"""
    view = agent_run_manager.store.get(run_id, current_user["user_id"])
    if not view:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return {"code": 200, "data": view.model_dump()}


@router.get("/projects/{project_id}/runs")
async def list_project_agent_runs(
    project_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """返回当前项目的普通 Agent 历史摘要；不混入 A/B 实验运行。"""
    user_id = current_user["user_id"]
    if projects.get_owned(project_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized.")
    items = agent_run_manager.store.list_project_history(project_id, user_id, limit=limit)
    return {"code": 200, "data": [item.model_dump() for item in items]}


@router.get("/runs/{run_id}/snapshot")
async def get_agent_run_snapshot(
    run_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """返回可重建问题、答案、时间线和证据的有界历史快照。"""
    snapshot = agent_run_manager.store.snapshot(run_id, current_user["user_id"])
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return {"code": 200, "data": snapshot.model_dump()}


@router.get("/runs/{run_id}/events")
async def stream_agent_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    current_user: dict[str, str] = Depends(get_current_user),
) -> StreamingResponse:
    """按持久化游标输出可续传 SSE，运行终止且事件发送完毕后结束。"""
    user_id = current_user["user_id"]
    store = agent_run_manager.store
    if not store.get(run_id, user_id):
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return sse_response(persisted_events(
        after=after,
        events_after=lambda sequence: store.events_after(run_id, sequence),
        current_view=lambda: store.get(run_id, user_id),
        terminal_statuses=TERMINAL_STATUSES,
    ))


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_run(run_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """持久化取消请求；排队任务立即取消，运行任务由持有租约的 Worker 终止。"""
    user_id = current_user["user_id"]
    view = agent_run_manager.store.get(run_id, user_id)
    if not view:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    if view.status in TERMINAL_STATUSES:
        return {"code": 200, "data": view.model_dump()}
    updated = agent_run_manager.store.request_cancel(run_id, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    agent_queue_worker.notify()
    return {
        "code": 200 if updated.status == "cancelled" else 202,
        "message": "Agent run cancelled." if updated.status == "cancelled" else "Cancellation requested.",
        "data": updated.model_dump(),
    }
