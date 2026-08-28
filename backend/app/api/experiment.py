"""依赖图 A/B 配对实验 HTTP 与 SSE 接口。

TEMPORARY CONTROL GROUP / 临时对照组：本路由包含盲态无图对照，删除边界见实验协议。
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.agents.worker import agent_queue_worker
from backend.app.core.deps import get_current_user
from backend.app.experiments import BlindReviewRequest, ComparisonRequest, ExperimentComparisonService, ExperimentError

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])
comparison_service = ExperimentComparisonService()


def _safe_call(callback):
    try:
        return callback()
    except ExperimentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc


@router.post("/projects/{project_id}/comparisons", status_code=202)
async def create_comparison(project_id: str, request: ComparisonRequest, current_user: dict = Depends(get_current_user)):
    """创建问题、模型、预算一致且展示顺序盲化的有图/无图配对运行。"""
    view = _safe_call(lambda: comparison_service.create(project_id, current_user["user_id"], request))
    agent_queue_worker.notify()
    return {"code": 202, "message": "Experiment comparison accepted.", "data": view}


@router.get("/comparisons/{comparison_id}")
async def get_comparison(comparison_id: str, current_user: dict = Depends(get_current_user)):
    """返回左右盲态运行、当前状态和可比较指标，不提前暴露实验组。"""
    view = _safe_call(lambda: comparison_service.get(comparison_id, current_user["user_id"]))
    return {"code": 200, "data": view}


@router.get("/comparisons/{comparison_id}/events")
async def comparison_events(comparison_id: str, current_user: dict = Depends(get_current_user)):
    """以快照 SSE 推送两个盲态运行状态，终态后结束。"""
    user_id = current_user["user_id"]
    _safe_call(lambda: comparison_service.get(comparison_id, user_id))

    async def event_stream():
        last_payload = ""
        last_heartbeat = time.monotonic()
        while True:
            view = comparison_service.get(comparison_id, user_id)
            encoded = json.dumps(view, ensure_ascii=False, default=str)
            if encoded != last_payload:
                yield f"event: comparison.snapshot\ndata: {encoded}\n\n"
                last_payload = encoded
            if view["status"] == "completed":
                break
            if time.monotonic() - last_heartbeat > 10:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/comparisons/{comparison_id}/review")
async def review_comparison(
    comparison_id: str,
    request: BlindReviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """保存揭盲前评分并返回左右实验组映射。"""
    result = _safe_call(lambda: comparison_service.review(comparison_id, current_user["user_id"], request))
    return {"code": 200, "message": "Blind review saved and comparison revealed.", "data": result}


@router.get("/comparisons/{comparison_id}/reveal")
async def reveal_comparison(comparison_id: str, current_user: dict = Depends(get_current_user)):
    """仅在提交盲评后返回左右组身份。"""
    result = _safe_call(lambda: comparison_service.reveal(comparison_id, current_user["user_id"]))
    return {"code": 200, "data": result}
