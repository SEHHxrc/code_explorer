"""恢复进程中断后遗留的项目删除隔离资源。"""

from __future__ import annotations

import logging

from backend.app.services.project_workspace import ProjectWorkspaceService

from .artifacts import ProjectArtifactRepository
from .deletion_journal import ProjectDeletionJournal, ProjectDeletionOperation
from .repository import ProjectRepository

logger = logging.getLogger(__name__)


class ProjectDeletionJanitor:
    """以数据库项目记录为提交事实，恢复或清理未完成删除事务。"""

    def __init__(
        self,
        *,
        projects: ProjectRepository | None = None,
        artifacts: ProjectArtifactRepository | None = None,
        workspace: ProjectWorkspaceService | None = None,
        journal: ProjectDeletionJournal | None = None,
    ) -> None:
        """注入项目、产物、工作区和删除日志边界。"""
        self.projects = projects or ProjectRepository()
        self.artifacts = artifacts or ProjectArtifactRepository()
        self.workspace = workspace or ProjectWorkspaceService()
        self.journal = journal or ProjectDeletionJournal()

    def cleanup_pending(self) -> dict[str, int]:
        """恢复数据库仍存在的项目，否则清理已提交删除的隔离资源。"""
        result = {"scanned": 0, "restored": 0, "purged": 0, "failed": 0}
        for operation in self.journal.list_pending():
            result["scanned"] += 1
            try:
                if self.projects.get_owned(operation.project_id, operation.user_id) is None:
                    self._purge(operation)
                    result["purged"] += 1
                else:
                    self._restore(operation)
                    result["restored"] += 1
                self.journal.remove(operation.operation_id)
            except Exception:
                result["failed"] += 1
                logger.exception("Unable to recover pending project deletion")
        return result

    def _restore(self, operation: ProjectDeletionOperation) -> None:
        """恢复尚未提交数据库删除的项目资源。"""
        self.artifacts.restore(operation.project_id, operation.operation_id)
        self.workspace.filesystem.restore_quarantined_project(
            operation.user_id,
            operation.project_id,
            operation.operation_id,
        )

    def _purge(self, operation: ProjectDeletionOperation) -> None:
        """清理数据库已提交删除后的隔离资源。"""
        self.workspace.filesystem.purge_project_quarantine(
            operation.user_id,
            operation.operation_id,
        )
        self.artifacts.purge_quarantine(operation.operation_id)
