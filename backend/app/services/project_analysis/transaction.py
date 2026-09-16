"""跨工作区、Artifact 和数据库提交边界的补偿事务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import TracebackType
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
        """返回补偿记录是否已经完成提交。"""
        return not self.failed_resources


@dataclass(frozen=True)
class _Compensation:
    """记录一次项目分析事务中可逆的资源创建状态。"""
    name: str
    callback: Callable[[], None]
    is_staging: bool = False


class ProjectAnalysisTransaction:
    """记录幂等补偿并在未提交退出时按逆序回收资源。"""

    def __init__(self, workspace: ProjectWorkspaceService, artifacts: AnalysisArtifactRepository) -> None:
        """输入工作区与产物仓储，初始化逆序补偿事务。"""
        self.workspace = workspace
        self.artifacts = artifacts
        self.operation: WorkspaceOperation | None = None
        self._compensations: list[_Compensation] = []
        self._committed = False

    def __enter__(self) -> "ProjectAnalysisTransaction":
        """进入项目分析补偿事务并返回自身。"""
        return self

    def __exit__(self, exc_type: type[BaseException] | None, _exc: BaseException | None, _traceback: TracebackType | None) -> bool:
        """离开事务时按异常和提交状态执行补偿清理。"""
        if not self._committed:
            self.rollback()
        return False

    def begin(self, user_id: str) -> WorkspaceOperation:
        """创建项目工作区操作并开始补偿跟踪。"""
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
        """记录已发布项目，供失败时逆序删除。"""
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"workspace:{operation.project_id}",
            callback=lambda: self.workspace.filesystem.remove_project(
                operation.user_id, operation.project_id,
            ),
        ))

    def track_artifact(self) -> None:
        """记录已保存产物，供失败时逆序删除。"""
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"artifact:{operation.project_id}",
            callback=lambda: self.artifacts.remove(operation.project_id),
        ))

    def transition(self, state: str) -> None:
        """更新当前工作区操作的持久化阶段。"""
        self.workspace.transition(self._require_operation(), state)

    def commit(self) -> None:
        """提交事务并停止后续自动补偿。"""
        operation = self._require_operation()
        # 数据库已在调用本方法前提交；从这一刻起不得因 Journal 清理失败回滚有效项目。
        self._committed = True
        try:
            self.workspace.transition(operation, "completed")
            self.workspace.filesystem.remove_operation(operation.user_id, operation.operation_id)
        except Exception:
            logger.exception("Committed project left a recoverable operation journal")

    def rollback(self) -> RollbackResult:
        """按产物、工作区的逆序执行幂等补偿。"""
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
        """返回当前操作；尚未开始时抛出运行错误。"""
        if self.operation is None:
            raise RuntimeError("Project analysis transaction has not begun")
        return self.operation