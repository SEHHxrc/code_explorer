# -*- coding: utf-8 -*-
"""项目导入、模型诊断、概览和清理的 HTTP 路由。"""

import math
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.app.core.deps import get_current_user
from backend.app.llm.diagnostics import list_available_models, probe_model_connection
from backend.app.llm.http import ModelRequestError
from backend.app.llm.registry import get_model_configuration, get_model_limits
from backend.app.schemas.manifest import ProjectManifest, ProjectOverviewRequest
from backend.app.schemas.project_analysis import ProjectAnalysisResponse
from backend.app.services.artifact_store import load_analysis_artifact
from backend.app.services.project_analysis import (
    AnalyzeProjectCommand,
    ProjectAnalysisError,
    ProjectAnalysisService,
    ProjectSource,
)
from backend.app.services.project_analysis.repository import ProjectRepository
from backend.app.services.project_lifecycle import ProjectLifecycleError, ProjectLifecycleService
from backend.app.services.project_inventory import ProjectInventoryError, ProjectInventoryService
from backend.app.services.project_overview import generate_project_overview
from backend.app.services.reports.overview_report import render_deterministic_overview

router = APIRouter(prefix="/api/projects", tags=["Projects"])
project_analysis_service = ProjectAnalysisService()
project_lifecycle_service = ProjectLifecycleService()
project_inventory_service = ProjectInventoryService()
project_repository = ProjectRepository()
MODEL_PROBE_COOLDOWN_SECONDS = 10.0
_model_probe_last_at: dict[str, float] = {}


class ModelProbeRequest(BaseModel):
    """模型连通性探测输入；模型为空时使用服务端默认配置。"""

    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )


def _model_probe_retry_after(user_id: str) -> int | None:
    """记录单进程用户探测时间；冷却期内返回需等待的秒数。"""
    now = time.monotonic()
    previous = _model_probe_last_at.get(user_id)
    if previous is not None and now - previous < MODEL_PROBE_COOLDOWN_SECONDS:
        return max(1, math.ceil(MODEL_PROBE_COOLDOWN_SECONDS - (now - previous)))
    _model_probe_last_at[user_id] = now
    if len(_model_probe_last_at) > 2_048:
        cutoff = now - MODEL_PROBE_COOLDOWN_SECONDS
        for key, timestamp in list(_model_probe_last_at.items()):
            if timestamp < cutoff:
                _model_probe_last_at.pop(key, None)
    return None


@router.get("")
async def list_projects(current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回当前用户的后端项目库存、资源完整性和存储占用。"""
    return {
        "code": 200,
        "message": "Project inventory loaded.",
        "data": await project_inventory_service.list(current_user["user_id"]),
    }


@router.post("/analyze", response_model=ProjectAnalysisResponse)
async def analyze_project(
    repo_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """校验 HTTP 输入并委托应用服务完成一次完整项目分析。"""
    if bool(repo_url) == bool(file):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one project source: repo_url or ZIP file.",
        )
    source = (
        ProjectSource.git(repo_url)
        if repo_url
        else ProjectSource.zip(file.file, file.filename)
    )
    try:
        result = await project_analysis_service.analyze(
            AnalyzeProjectCommand(user_id=current_user["user_id"], source=source)
        )
    except ProjectAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
    return {
        "code": 200,
        "message": "Project processed successfully.",
        "data": {
            "project_id": result.project_id,
            "sanitize_report": result.sanitize_report,
            "file_tree": result.file_tree,
            "dependency_graph": result.dependency_graph.model_dump(),
            "project_manifest": result.project_manifest.model_dump(),
            "project_overview": {
                "content": result.deterministic_overview,
                "source": "static",
                "provider": None,
                "model": None,
            },
        },
    }


@router.get("/model/status")
async def get_model_status(current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """输出不含密钥的模型配置状态；本接口不会产生外部请求。"""
    config = get_model_configuration()
    limits = get_model_limits()
    return {
        "code": 200,
        "data": {
            "configured": config.configured,
            "provider": config.provider if config.configured else None,
            "model": config.model if config.configured else None,
            "live_checked": False,
            "max_context_chars": limits.max_context_chars,
            "max_output_tokens": limits.max_output_tokens,
        },
    }


@router.post("/model/probe")
async def probe_model(
    request: ModelProbeRequest | None = None,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """执行一次用户显式触发的最小生成请求，返回安全连通性诊断。"""
    retry_after = _model_probe_retry_after(current_user["user_id"])
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"模型连接测试冷却中，请在 {retry_after} 秒后重试。",
            headers={"Retry-After": str(retry_after)},
        )
    return {
        "code": 200,
        "message": "Model connectivity probe completed.",
        "data": await probe_model_connection(request.model if request else None),
    }


@router.get("/model/models")
async def get_available_models(current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回当前模型凭据通过兼容 ``GET /models`` 可见的模型 ID。"""
    return {
        "code": 200,
        "message": "Visible model lookup completed.",
        "data": await list_available_models(),
    }


@router.get("/{project_id}/snapshot", response_model=ProjectAnalysisResponse)
async def restore_project_snapshot(
    project_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """从服务端工作区和分析产物恢复前端项目状态。"""
    try:
        data = await project_inventory_service.snapshot(project_id, current_user["user_id"])
    except ProjectInventoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
    return {
        "code": 200,
        "message": "Project snapshot restored.",
        "data": data,
    }


@router.post("/{project_id}/overview")
async def create_project_overview(
    project_id: str,
    request: ProjectOverviewRequest,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """输出有静态证据的概览；模型失败时返回静态结果。"""
    project = project_repository.get_owned(project_id, current_user["user_id"])

    if not project:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized.")

    artifact = load_analysis_artifact(project_id)
    if not artifact:
        raise HTTPException(status_code=409, detail="Project analysis artifact is missing.")
    manifest = ProjectManifest.model_validate(artifact["manifest"])
    try:
        result = await generate_project_overview(
            manifest=manifest,
            repo_map=artifact.get("repo_map", ""),
            use_model=request.use_model,
        )
    except ModelRequestError as exc:
        result = {
            "content": artifact.get("overview") or render_deterministic_overview(manifest),
            "source": "static_fallback",
            "provider": None,
            "model": None,
            "warning": f"模型调用失败，已返回静态分析结果：{exc.public_message}",
        }
    except Exception as exc:
        result = {
            "content": artifact.get("overview") or render_deterministic_overview(manifest),
            "source": "static_fallback",
            "provider": None,
            "model": None,
            "warning": f"模型调用失败，已返回静态分析结果：{type(exc).__name__}",
        }
    return {"code": 200, "message": "Project overview generated.", "data": result}


@router.delete("/clear/{project_id}")
async def clear_project(project_id: str, current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """删除当前用户的项目数据、工作目录和分析产物。"""
    try:
        result = await project_lifecycle_service.delete(project_id, current_user["user_id"])
        return {
            "code": 200,
            "message": f"Project {project_id} cleaned up successfully.",
            "data": {
                "project_id": result.project_id,
                "deleted": result.deleted,
                "warnings": result.warnings,
            },
        }
    except ProjectLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
