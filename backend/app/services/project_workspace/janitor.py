"""清理因进程终止而遗留的暂存操作和未提交资源。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from backend.app.models import ProjectModel, SessionLocal
from backend.app.services.artifact_store import remove_analysis_artifact

from .filesystem import WorkspaceFilesystem
from .journal import OperationJournal
from .paths import ProjectWorkspacePaths
from .policy import WorkspacePolicy

logger = logging.getLogger(__name__)


class WorkspaceJanitor:
    """通过受控 ID 重算路径，回收超时且未提交的导入操作。"""

    def __init__(self, *, paths=None, filesystem=None, journal=None, policy=None, session_factory=SessionLocal):
        self.paths = paths or ProjectWorkspacePaths()
        self.filesystem = filesystem or WorkspaceFilesystem(self.paths)
        self.journal = journal or OperationJournal()
        self.policy = policy or WorkspacePolicy()
        self.session_factory = session_factory

    def cleanup_stale(self) -> dict[str, int]:
        result = {"scanned": 0, "cleaned": 0, "failed": 0}
        if not self.paths.root.exists():
            return result
        cutoff = time.time() - self.policy.stale_operation_seconds
        for user_root in self.paths.root.iterdir():
            staging = user_root / ".staging"
            if not staging.is_dir():
                continue
            for operation_root in staging.iterdir():
                if not operation_root.is_dir() or operation_root.stat().st_mtime >= cutoff:
                    continue
                result["scanned"] += 1
                try:
                    payload = self.journal.read(operation_root)
                    if payload:
                        user_id = self.paths.validate_identifier(str(payload.get("user_id", "")), "user id")
                        project_id = self.paths.validate_identifier(str(payload.get("project_id", "")), "project id")
                        operation_id = self.paths.validate_identifier(str(payload.get("operation_id", "")), "operation id")
                        if user_id != user_root.name or operation_id != operation_root.name:
                            raise ValueError("Operation journal identity mismatch")
                        if not self._project_exists(project_id, user_id):
                            self.filesystem.remove_project(user_id, project_id)
                            remove_analysis_artifact(project_id)
                    else:
                        operation_id = self.paths.validate_identifier(operation_root.name, "operation id")
                        user_id = self.paths.validate_identifier(user_root.name, "user id")
                    self.filesystem.remove_operation(user_id, operation_id)
                    result["cleaned"] += 1
                except Exception:
                    result["failed"] += 1
                    logger.exception("Failed to clean stale workspace operation")
        return result

    def _project_exists(self, project_id: str, user_id: str) -> bool:
        session = self.session_factory()
        try:
            return session.query(ProjectModel.id).filter(
                ProjectModel.id == project_id,
                ProjectModel.user_id == user_id,
            ).first() is not None
        finally:
            session.close()

