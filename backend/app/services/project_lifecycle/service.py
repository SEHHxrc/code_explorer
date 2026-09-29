"""已持久化项目的所有权检查和一致删除服务。"""

from __future__ import annotations

import asyncio

from backend.app.services.project_analysis.artifact_repository import (
    AnalysisArtifactRepository,
)
from backend.app.services.project_analysis.repository import ProjectRepository
from backend.app.services.project_workspace import ProjectWorkspaceService

from .contracts import ProjectDeletionResult, ProjectLifecycleError


class ProjectLifecycleService:
    """在拒绝活动任务后删除文件资源，最后提交数据库删除。"""

    def __init__(
        self,
        *,
        project_repository: ProjectRepository | None = None,
        artifact_repository: AnalysisArtifactRepository | None = None,
        workspace_service: ProjectWorkspaceService | None = None,
    ) -> None:
        """注入项目、产物和工作区边界，初始化一致删除服务。"""
        self.projects = project_repository or ProjectRepository()
        self.artifacts = artifact_repository or AnalysisArtifactRepository()
        self.workspace = workspace_service or ProjectWorkspaceService()

    async def delete(self, project_id: str, user_id: str) -> ProjectDeletionResult:
        """异步删除当前用户拥有的项目及其关联资源。"""
        return await asyncio.to_thread(self._delete_sync, project_id, user_id)

    def _delete_sync(self, project_id: str, user_id: str) -> ProjectDeletionResult:
        """按活动任务、文件、产物和数据库顺序删除项目。"""
        project = self.projects.get_owned(project_id, user_id)
        if project is None:
            raise ProjectLifecycleError("Project not found or unauthorized.", 404)
        if self.projects.has_active_runs(project_id, user_id):
            raise ProjectLifecycleError(
                "Project has an active agent or execution task; cancel it before deleting the project.",
                409,
            )
        try:
            self.workspace.filesystem.remove_project(user_id, project_id)
            self.artifacts.remove(project_id)
            if not self.projects.delete_owned_with_runs(project_id, user_id):
                raise RuntimeError("Project disappeared during deletion")
        except Exception as exc:
            raise ProjectLifecycleError("Unable to delete all project resources safely.", 500) from exc
        return ProjectDeletionResult(project_id=project_id)
