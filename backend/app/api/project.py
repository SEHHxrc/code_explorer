"""项目导入、查询、概览和清理的 HTTP 路由。"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.app.core.deps import get_current_user
from backend.app.llm.http import ModelRequestError
from backend.app.schemas.manifest import ProjectManifest, ProjectOverviewRequest
from backend.app.schemas.project_analysis import ProjectAnalysisResponse
from backend.app.services.projects import (
    AnalyzeProjectCommand,
    ProjectArtifactRepository,
    ProjectAnalysisError,
    ProjectDeletionError,
    ProjectDeletionService,
    ProjectImportService,
    ProjectQueryError,
    ProjectQueryService,
    ProjectRepository,
    ProjectSource,
)
from backend.app.services.projects.progress import ImportProgressStore
from backend.app.services.project_overview import generate_project_overview
from backend.app.services.reports.overview_report import render_deterministic_overview

router = APIRouter(prefix="/api/projects", tags=["Projects"])
project_import_service = ProjectImportService()
project_deletion_service = ProjectDeletionService()
project_query_service = ProjectQueryService()
project_repository = ProjectRepository()
project_artifacts = ProjectArtifactRepository()
import_progress = ImportProgressStore()


@router.post("/analysis-progress")
async def create_analysis_progress(
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """上传前登记当前用户的进度任务；不创建项目或启动分析。"""
    try:
        request_id = import_progress.create(current_user["user_id"])
    except ProjectAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
    return {"code": 200, "data": {"request_id": request_id}}


@router.get("/analysis-progress/{request_id}")
async def get_analysis_progress(
    request_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """读取本人任务的阶段和真实文件进度；不能读取其他用户的任务。"""
    try:
        data = import_progress.snapshot(request_id, current_user["user_id"])
    except ProjectAnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
    return {"code": 200, "data": data}


@router.get("")
async def list_projects(current_user: dict[str, str] = Depends(get_current_user)) -> dict[str, Any]:
    """返回当前用户的后端项目库存、资源完整性和存储占用。"""
    return {
        "code": 200,
        "message": "Project inventory loaded.",
        "data": await project_query_service.list(current_user["user_id"]),
    }


@router.post("/analyze", response_model=ProjectAnalysisResponse)
async def analyze_project(
    repo_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    request_id: str | None = Form(default=None, max_length=64),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """校验 HTTP 输入并委托应用服务完成一次完整项目分析。"""
    if bool(repo_url) == bool(file):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one project source: repo_url or ZIP file.",
        )
    if repo_url:
        source = ProjectSource.git(repo_url)
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="ZIP file is required.")
        source = ProjectSource.zip(file.file, file.filename)
    try:
        progress = import_progress.claim(request_id, current_user["user_id"]) if request_id else None
        result = await project_import_service.import_project(
            AnalyzeProjectCommand(user_id=current_user["user_id"], source=source),
            **({"progress": progress} if progress is not None else {}),
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


@router.get("/{project_id}/snapshot", response_model=ProjectAnalysisResponse)
async def restore_project_snapshot(
    project_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """从服务端工作区和分析产物恢复前端项目状态。"""
    try:
        data = await project_query_service.snapshot(project_id, current_user["user_id"])
    except ProjectQueryError as exc:
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

    artifact = project_artifacts.load(project_id)
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
        result = await project_deletion_service.delete(project_id, current_user["user_id"])
        return {
            "code": 200,
            "message": f"Project {project_id} cleaned up successfully.",
            "data": {
                "project_id": result.project_id,
                "deleted": result.deleted,
                "warnings": result.warnings,
            },
        }
    except ProjectDeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
