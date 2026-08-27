"""跨工作区、Artifact 和数据库提交边界的补偿事务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspaceOperation

from .artifact_repository import AnalysisArtifactRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollbackResult:
    """一次补偿执行的成功项和失败项。"""

    removed_resources: list[str] = field(default_factory=list)
    failed_resources: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return not self.failed_resources


@dataclass(frozen=True)
class _Compensation:
    name: str
    callback: Callable[[], None]
    is_staging: bool = False


class ProjectAnalysisTransaction:
    """记录幂等补偿并在未提交退出时按逆序回收资源。"""

    def __init__(self, workspace: ProjectWorkspaceService, artifacts: AnalysisArtifactRepository):
        self.workspace = workspace
        self.artifacts = artifacts
        self.operation: WorkspaceOperation | None = None
        self._compensations: list[_Compensation] = []
        self._committed = False

    def __enter__(self) -> "ProjectAnalysisTransaction":
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if not self._committed:
            self.rollback()
        return False

    def begin(self, user_id: str) -> WorkspaceOperation:
        self.operation = self.workspace.begin(user_id)
        operation = self.operation
        self._compensations.append(_Compensation(
            name=f"staging:{operation.operation_id}",
            callback=lambda: self.workspace.filesystem.remove_operation(
                operation.user_id, operation.operation_id,
            ),
            is_staging=True,
        ))
        return operation

    def track_project(self) -> None:
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"workspace:{operation.project_id}",
            callback=lambda: self.workspace.filesystem.remove_project(
                operation.user_id, operation.project_id,
            ),
        ))

    def track_artifact(self) -> None:
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"artifact:{operation.project_id}",
            callback=lambda: self.artifacts.remove(operation.project_id),
        ))

    def transition(self, state: str) -> None:
        self.workspace.transition(self._require_operation(), state)

    def commit(self) -> None:
        operation = self._require_operation()
        # 数据库已在调用本方法前提交；从这一刻起不得因 Journal 清理失败回滚有效项目。
        self._committed = True
        try:
            self.workspace.transition(operation, "completed")
            self.workspace.filesystem.remove_operation(operation.user_id, operation.operation_id)
        except Exception:
            logger.exception("Committed project left a recoverable operation journal")

    def rollback(self) -> RollbackResult:
        operation = self.operation
        if operation is None:
            return RollbackResult()
        try:
            self.workspace.transition(operation, "rolling_back")
        except Exception:
            logger.exception("Unable to mark project analysis rollback")
        removed: list[str] = []
        failed: list[str] = []
        for compensation in reversed(self._compensations):
            if compensation.is_staging and failed:
                continue
            try:
                compensation.callback()
                removed.append(compensation.name)
            except Exception:
                failed.append(compensation.name)
                logger.exception("Project analysis compensation failed: %s", compensation.name)
        if failed:
            try:
                self.workspace.mark_rollback_failed(operation)
            except Exception:
                logger.exception("Unable to persist rollback failure state")
        return RollbackResult(removed_resources=removed, failed_resources=failed)

    def _require_operation(self) -> WorkspaceOperation:
        if self.operation is None:
            raise RuntimeError("Project analysis transaction has not begun")
        return self.operation