"""项目暂存、获取、清洗和原子发布服务。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .contracts import PreparedWorkspace, WorkspaceOperation, WorkspaceSource
from .exceptions import SourceValidationError, WorkspacePublishError
from .filesystem import WorkspaceFilesystem
from .journal import OperationJournal
from .paths import ProjectWorkspacePaths
from .policy import WorkspacePolicy
from .sanitizer import ProjectSanitizer
from .sources import GitProjectSource, ZipProjectSource


def _default_policy() -> WorkspacePolicy:
    configured_hosts = {
        item.strip().casefold()
        for item in os.getenv("GIT_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    return WorkspacePolicy(allowed_git_hosts=frozenset(configured_hosts))


class ProjectWorkspaceService:
    """让不可信项目只在暂存区中获取和清洗，成功后才发布。"""

    def __init__(
        self,
        *,
        policy: WorkspacePolicy | None = None,
        paths: ProjectWorkspacePaths | None = None,
        filesystem: WorkspaceFilesystem | None = None,
        journal: OperationJournal | None = None,
    ):
        self.policy = policy or _default_policy()
        self.paths = paths or ProjectWorkspacePaths()
        self.filesystem = filesystem or WorkspaceFilesystem(self.paths)
        self.journal = journal or OperationJournal()
        self.sanitizer = ProjectSanitizer(self.policy, self.filesystem)
        self.git_source = GitProjectSource(self.policy, self.filesystem)
        self.zip_source = ZipProjectSource(self.policy)

    def begin(self, user_id: str) -> WorkspaceOperation:
        operation_id = uuid.uuid4().hex
        project_id = uuid.uuid4().hex
        operation_root, source_root = self.filesystem.create_operation(user_id, operation_id)
        operation = WorkspaceOperation(
            operation_id=operation_id,
            project_id=project_id,
            user_id=user_id,
            operation_root=operation_root,
            source_root=source_root,
            final_root=self.paths.project_root(user_id, project_id),
        )
        try:
            self.journal.create(operation)
        except Exception:
            self.filesystem.remove_operation(user_id, operation_id)
            raise
        return operation

    def prepare(self, operation: WorkspaceOperation, source: WorkspaceSource) -> PreparedWorkspace:
        self.journal.transition(operation, "acquiring")
        if source.kind == "git" and source.repo_url and source.file_obj is None:
            source_tag = self.git_source.acquire(source.repo_url, operation.source_root)
        elif source.kind == "zip" and source.file_obj is not None and source.repo_url is None:
            source_tag = self.zip_source.acquire(
                source.file_obj,
                source.filename,
                operation.operation_root,
                operation.source_root,
            )
        else:
            raise SourceValidationError("Provide exactly one project source: repo_url or ZIP file.")
        self.journal.transition(operation, "sanitizing")
        report = self.sanitizer.clean(operation.source_root)
        return PreparedWorkspace(operation=operation, source_tag=source_tag, sanitize_report=report)

    def publish(self, prepared: PreparedWorkspace) -> Path:
        operation = prepared.operation
        self.journal.transition(operation, "publishing")
        try:
            return self.filesystem.publish(
                operation.user_id,
                operation.project_id,
                operation.source_root,
            )
        except Exception as exc:
            raise WorkspacePublishError() from exc

    def transition(self, operation: WorkspaceOperation, state: str) -> None:
        self.journal.transition(operation, state)

    def finish(self, operation: WorkspaceOperation) -> None:
        self.journal.transition(operation, "completed")
        self.filesystem.remove_operation(operation.user_id, operation.operation_id)

    def mark_rollback_failed(self, operation: WorkspaceOperation) -> None:
        self.journal.transition(operation, "rollback_failed")

