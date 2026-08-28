# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import TERMINAL_STATUSES, agent_run_manager
from backend.app.agents.worker import agent_queue_worker
from backend.app.core.deps import get_current_user
from backend.app.models import ProjectModel, SessionLocal
from backend.app.services.artifact_store import load_analysis_artifact


router = APIRouter(prefix="/api/agent", tags=["Agent"])


def _project_for_user(project_id: str, user_id: str) -> ProjectModel | None:
    db = SessionLocal()
    try:
        return db.query(ProjectModel).filter(
            ProjectModel.id == project_id,
            ProjectModel.user_id == user_id,
        ).first()
    finally:
        db.close()


@router.post("/projects/{project_id}/runs")
async def create_agent_run(
    project_id: str,
    request: AgentRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """输入项目、运行请求和当前用户，创建后台运行并输出 202 视图及 SSE 地址。"""
    project = _project_for_user(project_id, current_user["user_id"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized.")
    artifact = load_analysis_artifact(project_id)
    if not artifact:
        raise HTTPException(status_code=409, detail="Project analysis artifact is missing.")
    run_id = uuid.uuid4().hex
    view = agent_run_manager.store.create(
        run_id=run_id,
        project_id=project_id,
        user_id=current_user["user_id"],
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
async def get_agent_run(run_id: str, current_user: dict = Depends(get_current_user)):
    """输入运行 ID 和当前用户，输出该用户可见的最新运行状态。"""
    view = agent_run_manager.store.get(run_id, current_user["user_id"])
    if not view:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return {"code": 200, "data": view.model_dump()}


@router.get("/runs/{run_id}/events")
async def stream_agent_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """输入运行与已消费序号，输出可续传的 SSE 事件流，运行终止后结束。"""
    if not agent_run_manager.store.get(run_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Agent run not found.")

    async def event_stream():
        """按序号轮询持久化事件并生成 SSE 文本块。"""
        sequence = after
        last_heartbeat = time.monotonic()
        while True:
            events = agent_run_manager.store.events_after(run_id, sequence)
            for event in events:
                sequence = event.sequence
                payload = json.dumps(event.model_dump(), ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            view = agent_run_manager.store.get(run_id, current_user["user_id"])
            if view is None or (view.status in TERMINAL_STATUSES and not events):
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


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_run(run_id: str, current_user: dict = Depends(get_current_user)):
    """持久化取消请求；排队任务立即取消，运行任务由持有租约的 Worker 终止。"""
    view = agent_run_manager.store.get(run_id, current_user["user_id"])
    if not view:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    if view.status in TERMINAL_STATUSES:
        return {"code": 200, "data": view.model_dump()}
    updated = agent_run_manager.store.request_cancel(run_id, current_user["user_id"])
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    agent_queue_worker.notify()
    return {
        "code": 200 if updated.status == "cancelled" else 202,
        "message": "Agent run cancelled." if updated.status == "cancelled" else "Cancellation requested.",
        "data": updated.model_dump(),
    }
