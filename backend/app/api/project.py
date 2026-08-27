# -*- coding: utf-8 -*-
"""项目导入、概览和清理的 HTTP 路由。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.app.core.deps import get_current_user
from backend.app.llm.registry import get_model_configuration
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
from backend.app.services.project_overview import generate_project_overview
from backend.app.services.reports.overview_report import render_deterministic_overview

router = APIRouter(prefix="/api/projects", tags=["Projects"])
project_analysis_service = ProjectAnalysisService()
project_lifecycle_service = ProjectLifecycleService()
project_repository = ProjectRepository()


@router.post("/analyze", response_model=ProjectAnalysisResponse)
async def analyze_project(
    repo_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    current_user: dict = Depends(get_current_user),
):
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
async def get_model_status(current_user: dict = Depends(get_current_user)):
    """输出不含密钥的模型配置状态、供应商和模型名。"""
    config = get_model_configuration()
    return {
        "code": 200,
        "data": {
            "configured": config.configured,
            "provider": config.provider if config.configured else None,
            "model": config.model if config.configured else None,
        },
    }


@router.post("/{project_id}/overview")
async def create_project_overview(
    project_id: str,
    request: ProjectOverviewRequest,
    current_user: dict = Depends(get_current_user),
):
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
async def clear_project(project_id: str, current_user: dict = Depends(get_current_user)):
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