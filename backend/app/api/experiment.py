"""静态安全证据 A/B 配对实验 HTTP 与 SSE 接口。

TEMPORARY CONTROL GROUP / 临时对照组：本路由包含盲态无图对照，删除边界见实验协议。
"""

from __future__ import annotations

import asyncio
import json
import time
import os
import hmac
import ipaddress
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query
from fastapi.responses import StreamingResponse

from backend.app.agents.worker import agent_queue_worker
from backend.app.api.sse import sse_response
from backend.app.core.deps import get_current_user
from backend.app.experiments import (
    BlindReviewRequest,
    ComparisonRequest,
    ExperimentComparisonService,
    ExperimentError,
)

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])
T = TypeVar("T")
comparison_service = ExperimentComparisonService()


def require_local_audit_access(request: Request, token: str | None) -> None:
    """敏感审计默认禁用；仅本机、独立管理员口令和项目所有权共同放行。"""
    enabled = os.getenv("EXPERIMENT_AUDIT_READ_ENABLED", "0") == "1"
    secret = os.getenv("EXPERIMENT_AUDIT_READ_TOKEN", "")
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not enabled or not local or len(secret) < 32 or not token or not hmac.compare_digest(secret.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="完整审计仅允许显式启用的本机管理员访问。")


@router.get("/comparisons/{comparison_id}/audit/{lane}")
async def read_comparison_audit(
    comparison_id: str, lane: Literal["left", "right"], request: Request,
    acknowledge_unblind: bool = False,
    x_audit_token: str | None = Header(default=None),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """揭盲后显式读取本人实验的脱敏审计；普通 SSE 和历史快照不携带正文。"""
    require_local_audit_access(request, x_audit_token)
    user_id = current_user["user_id"]
    record = _safe_call(lambda: comparison_service._record(comparison_id, user_id))
    if not comparison_service.repository.has_review(comparison_id, user_id):
        view = _safe_call(lambda: comparison_service.get(comparison_id, user_id))
        if not (acknowledge_unblind and view["status"] == "completed" and not view["valid_for_review"]):
            raise HTTPException(status_code=409, detail="必须先完成盲评；无效终态实验可显式确认揭盲后诊断。")
    from backend.app.experiments.audit import AuditRepository
    strategy = record.blind_order[lane]
    run_id = record.baseline_run_id if strategy == "baseline" else record.graph_run_id
    data = await asyncio.to_thread(AuditRepository(comparison_service.repository.session_factory).read, run_id, user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="历史运行未启用审计，不会回填。")
    return {"code": 200, "data": data}


def _safe_call(callback: Callable[[], T]) -> T:
    """把领域错误转换为稳定的 HTTP 错误响应。"""
    try:
        return callback()
    except ExperimentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc


@router.post("/projects/{project_id}/comparisons", status_code=202)
async def create_comparison(
    project_id: str,
    request: ComparisonRequest,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """创建问题、模型、预算一致且展示顺序盲化的安全证据/原始文件配对。"""
    view = _safe_call(lambda: comparison_service.create(project_id, current_user["user_id"], request))
    agent_queue_worker.notify()
    return {"code": 202, "message": "Experiment comparison accepted.", "data": view}


@router.get("/comparisons/{comparison_id}")
async def get_comparison(comparison_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回左右盲态运行、当前状态和可比较指标，不提前暴露实验组。"""
    view = _safe_call(lambda: comparison_service.get(comparison_id, current_user["user_id"]))
    return {"code": 200, "data": view}


@router.get("/projects/{project_id}/comparisons")
async def list_comparison_history(
    project_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """分页读取本人项目全部实验摘要；不携带完整审计或提前揭盲。"""
    return {"code": 200, "data": _safe_call(lambda: comparison_service.history(
        project_id, current_user["user_id"], offset=offset, limit=limit,
    ))}


@router.get("/comparisons/{comparison_id}/history")
async def get_comparison_history(
    comparison_id: str, current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """读取原始结果及共同提示词快照；完整传输审计仍走独立管理员权限。"""
    return {"code": 200, "data": _safe_call(lambda: comparison_service.history_detail(comparison_id, current_user["user_id"]))}


@router.get("/comparisons/{comparison_id}/events")
async def comparison_events(comparison_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> StreamingResponse:
    """以快照 SSE 推送两个盲态运行状态，终态后结束。"""
    user_id = current_user["user_id"]
    _safe_call(lambda: comparison_service.get(comparison_id, user_id))

    async def event_stream() -> AsyncIterator[str]:
        """轮询配对实验快照并生成 SSE 文本块。"""
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

    return sse_response(event_stream())



@router.post("/comparisons/{comparison_id}/review")
async def review_comparison(
    comparison_id: str,
    request: BlindReviewRequest,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """保存揭盲前评分并返回左右实验组映射。"""
    result = _safe_call(lambda: comparison_service.review(comparison_id, current_user["user_id"], request))
    return {"code": 200, "message": "Blind review saved and comparison revealed.", "data": result}


@router.get("/comparisons/{comparison_id}/reveal")
async def reveal_comparison(comparison_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """仅在提交盲评后返回左右组身份。"""
    result = _safe_call(lambda: comparison_service.reveal(comparison_id, current_user["user_id"]))
    return {"code": 200, "data": result}
