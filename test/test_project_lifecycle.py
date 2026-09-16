# -*- coding: utf-8 -*-
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.services.project_lifecycle import ProjectLifecycleError, ProjectLifecycleService
from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspacePolicy
from backend.app.services.project_workspace.contracts import PreparedWorkspace, SanitizeReport
from backend.app.services.project_workspace.janitor import WorkspaceJanitor
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


class FakeProjects:
    def __init__(self, *, active=False, delete_fails=False):
        self.active = active
        self.delete_fails = delete_fails
        self.deleted = False
        self.record = SimpleNamespace(project_id="project-1", user_id="user-1")

    def get_owned(self, project_id, user_id):
        return self.record if (project_id, user_id) == ("project-1", "user-1") else None

    def has_active_runs(self, project_id, user_id):
        return self.active

    def delete_owned_with_runs(self, project_id, user_id):
        if self.delete_fails:
            raise RuntimeError("database unavailable")
        self.deleted = True
        return True


class FakeArtifacts:
    def __init__(self):
        self.removed = []

    def remove(self, project_id):
        self.removed.append(project_id)


class EmptyQuery:
    def filter(self, *args):
        return self

    def first(self):
        return None


class EmptySession:
    def query(self, *args):
        return EmptyQuery()

    def close(self):
        pass


class ProjectLifecycleTests(unittest.TestCase):
    def _workspace(self, root):
        return ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(root) / "users"))

    def test_active_agent_run_blocks_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = self._workspace(temp)
            target = workspace.paths.project_root("user-1", "project-1")
            target.mkdir(parents=True)
            service = ProjectLifecycleService(
                project_repository=FakeProjects(active=True),
                artifact_repository=FakeArtifacts(),
                workspace_service=workspace,
            )
            with self.assertRaises(ProjectLifecycleError) as caught:
                asyncio.run(service.delete("project-1", "user-1"))
            self.assertEqual(409, caught.exception.status_code)
            self.assertTrue(target.exists())

    def test_success_deletes_files_artifact_then_database(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = self._workspace(temp)
            target = workspace.paths.project_root("user-1", "project-1")
            target.mkdir(parents=True)
            (target / "main.py").write_text("pass", encoding="utf-8")
            projects = FakeProjects()
            artifacts = FakeArtifacts()
            result = asyncio.run(ProjectLifecycleService(
                project_repository=projects,
                artifact_repository=artifacts,
                workspace_service=workspace,
            ).delete("project-1", "user-1"))
            self.assertTrue(result.deleted)
            self.assertFalse(target.exists())
            self.assertEqual(["project-1"], artifacts.removed)
            self.assertTrue(projects.deleted)

    def test_janitor_removes_stale_uncommitted_published_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = self._workspace(temp)
            operation = workspace.begin("user-1")
            (operation.source_root / "main.py").write_text("pass", encoding="utf-8")
            prepared = PreparedWorkspace(operation, "test", SanitizeReport(scanned_files=1))
            final_path = workspace.publish(prepared)
            old = time.time() - 100
            os.utime(operation.operation_root, (old, old))
            janitor = WorkspaceJanitor(
                paths=workspace.paths,
                filesystem=workspace.filesystem,
                journal=workspace.journal,
                policy=WorkspacePolicy(stale_operation_seconds=1),
                session_factory=EmptySession,
            )
            result = janitor.cleanup_stale()
            self.assertEqual(1, result["cleaned"])
            self.assertFalse(final_path.exists())
            self.assertFalse(operation.operation_root.exists())


if __name__ == "__main__":
    unittest.main()