"""项目资源隔离、数据库删除和失败恢复服务。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from backend.app.services.project_workspace import ProjectWorkspaceService

from .artifacts import ProjectArtifactRepository
from .contracts import ProjectDeletionResult
from .deletion_journal import ProjectDeletionJournal
from .errors import ProjectDeletionError
from .repository import ProjectDeletionRepository, ProjectRepository

logger = logging.getLogger(__name__)


class ProjectDeletionService:
    """先隔离文件资源，再提交数据库删除；提交前失败时恢复资源。"""

    def __init__(
        self,
        *,
        projects: ProjectRepository | None = None,
        deletion_repository: ProjectDeletionRepository | None = None,
        artifacts: ProjectArtifactRepository | None = None,
        workspace: ProjectWorkspaceService | None = None,
        journal: ProjectDeletionJournal | None = None,
    ) -> None:
        """注入项目、删除事务、产物和工作区边界。"""
        self.projects = projects or ProjectRepository()
        self.deletions = deletion_repository or ProjectDeletionRepository()
        self.artifacts = artifacts or ProjectArtifactRepository()
        self.workspace = workspace or ProjectWorkspaceService()
        self.journal = journal or ProjectDeletionJournal()

    async def delete(self, project_id: str, user_id: str) -> ProjectDeletionResult:
        """在线程池执行项目资源和数据库的一致删除。"""
        return await asyncio.to_thread(self._delete_sync, project_id, user_id)

    def _delete_sync(self, project_id: str, user_id: str) -> ProjectDeletionResult:
        """隔离资源、提交数据库删除并清理隔离区。"""
        if self.projects.get_owned(project_id, user_id) is None:
            raise ProjectDeletionError("Project not found or unauthorized.", 404)
        if self.deletions.has_active_tasks(project_id, user_id):
            raise ProjectDeletionError(
                "Project has an active agent or execution task; cancel it before deleting the project.",
                409,
            )
        operation_id = uuid.uuid4().hex
        try:
            operation = self.journal.begin(operation_id, project_id, user_id)
        except Exception as exc:
            raise ProjectDeletionError("Unable to begin the recoverable project deletion.", 500) from exc
        workspace_quarantined = False
        artifact_quarantined = False
        warnings: list[str] = []
        try:
            workspace_quarantined = self.workspace.filesystem.quarantine_project(
                user_id,
                project_id,
                operation_id,
            )
            artifact_quarantined = self.artifacts.quarantine(project_id, operation_id)
            operation = self.journal.transition(operation, "quarantined")
            if not self.deletions.delete_owned_with_dependents(project_id, user_id):
                raise RuntimeError("Project disappeared during deletion")
            try:
                operation = self.journal.transition(operation, "database_deleted")
            except Exception:
                logger.exception("Committed project deletion journal transition failed")
                warnings.append("Deletion journal requires background cleanup.")
        except Exception as exc:
            restoration_failures = self._restore(
                project_id=project_id,
                user_id=user_id,
                operation_id=operation_id,
                workspace_quarantined=workspace_quarantined,
                artifact_quarantined=artifact_quarantined,
            )
            if not restoration_failures:
                try:
                    self.journal.remove(operation_id)
                except Exception:
                    logger.exception("Unable to remove rolled-back project deletion journal")
            message = "Unable to delete all project resources safely."
            if restoration_failures:
                message = "Project deletion failed and some isolated resources require recovery."
            raise ProjectDeletionError(message, 500) from exc

        try:
            self.workspace.filesystem.purge_project_quarantine(user_id, operation_id)
        except Exception:
            logger.exception("Unable to purge committed project workspace deletion")
            warnings.append("Workspace deletion quarantine requires background cleanup.")
        try:
            self.artifacts.purge_quarantine(operation_id)
        except Exception:
            logger.exception("Unable to purge committed project artifact deletion")
            warnings.append("Artifact deletion quarantine requires background cleanup.")
        if not warnings:
            try:
                self.journal.remove(operation_id)
            except Exception:
                logger.exception("Unable to remove completed project deletion journal")
                warnings.append("Deletion journal requires background cleanup.")
        else:
            try:
                self.journal.transition(operation, "cleanup_pending")
            except Exception:
                logger.exception("Unable to mark project deletion cleanup pending")
        return ProjectDeletionResult(project_id=project_id, warnings=warnings)

    def _restore(
        self,
        *,
        project_id: str,
        user_id: str,
        operation_id: str,
        workspace_quarantined: bool,
        artifact_quarantined: bool,
    ) -> list[str]:
        """数据库提交前失败时尽力恢复隔离资源并返回失败项。"""
        failures: list[str] = []
        if artifact_quarantined:
            try:
                self.artifacts.restore(project_id, operation_id)
            except Exception:
                logger.exception("Unable to restore quarantined project artifact")
                failures.append("artifact")
        if workspace_quarantined:
            try:
                self.workspace.filesystem.restore_quarantined_project(
                    user_id,
                    project_id,
                    operation_id,
                )
            except Exception:
                logger.exception("Unable to restore quarantined project workspace")
                failures.append("workspace")
        return failures


__all__ = ["ProjectDeletionService"]
