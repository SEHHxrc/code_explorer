"""项目导入跨工作区和 Artifact 边界的补偿事务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import TracebackType
from typing import Callable, Literal

from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspaceOperation

from .artifacts import ProjectArtifactRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollbackResult:
    """一次补偿执行的成功项和失败项。"""

    removed_resources: list[str] = field(default_factory=list)
    failed_resources: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """返回补偿动作是否全部成功。"""
        return not self.failed_resources


@dataclass(frozen=True)
class _Compensation:
    """记录项目导入事务中的一个幂等补偿动作。"""

    name: str
    callback: Callable[[], None]
    is_staging: bool = False


class ProjectImportTransaction:
    """记录项目导入资源并在未提交退出时逆序补偿。"""

    def __init__(
        self,
        workspace: ProjectWorkspaceService,
        artifacts: ProjectArtifactRepository,
    ) -> None:
        """输入工作区和产物仓储，初始化补偿事务。"""
        self.workspace = workspace
        self.artifacts = artifacts
        self.operation: WorkspaceOperation | None = None
        self._compensations: list[_Compensation] = []
        self._committed = False

    def __enter__(self) -> ProjectImportTransaction:
        """进入补偿事务并返回自身。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        """未提交退出时执行逆序补偿。"""
        if not self._committed:
            self.rollback()
        return False

    def begin(self, user_id: str) -> WorkspaceOperation:
        """创建暂存操作并登记暂存目录补偿。"""
        self.operation = self.workspace.begin(user_id)
        operation = self.operation
        self._compensations.append(_Compensation(
            name=f"staging:{operation.operation_id}",
            callback=lambda: self.workspace.filesystem.remove_operation(
                operation.user_id,
                operation.operation_id,
            ),
            is_staging=True,
        ))
        return operation

    def track_published_workspace(self) -> None:
        """登记可能已经发布的项目工作区补偿。"""
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"workspace:{operation.project_id}",
            callback=lambda: self.workspace.filesystem.remove_project(
                operation.user_id,
                operation.project_id,
            ),
        ))

    def track_project(self) -> None:
        """兼容旧名称，登记已发布项目工作区补偿。"""
        self.track_published_workspace()

    def track_artifact(self) -> None:
        """登记可能已经保存的分析产物补偿。"""
        operation = self._require_operation()
        self._compensations.append(_Compensation(
            name=f"artifact:{operation.project_id}",
            callback=lambda: self.artifacts.remove(operation.project_id),
        ))

    def transition(self, state: str) -> None:
        """更新当前导入操作的持久化阶段。"""
        self.workspace.transition(self._require_operation(), state)

    def commit(self) -> None:
        """提交事务；数据库已提交后不因日志清理失败回滚有效项目。"""
        operation = self._require_operation()
        self._committed = True
        try:
            self.workspace.transition(operation, "completed")
            self.workspace.filesystem.remove_operation(operation.user_id, operation.operation_id)
        except Exception:
            logger.exception("Committed project left a recoverable operation journal")

    def rollback(self) -> RollbackResult:
        """按登记顺序的逆序执行幂等补偿。"""
        operation = self.operation
        if operation is None:
            return RollbackResult()
        try:
            self.workspace.transition(operation, "rolling_back")
        except Exception:
            logger.exception("Unable to mark project import rollback")
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
                logger.exception("Project import compensation failed: %s", compensation.name)
        if failed:
            try:
                self.workspace.mark_rollback_failed(operation)
            except Exception:
                logger.exception("Unable to persist rollback failure state")
        return RollbackResult(removed_resources=removed, failed_resources=failed)

    def _require_operation(self) -> WorkspaceOperation:
        """返回当前操作；尚未开始时抛出运行错误。"""
        if self.operation is None:
            raise RuntimeError("Project import transaction has not begun")
        return self.operation


__all__ = ["ProjectImportTransaction", "RollbackResult"]
