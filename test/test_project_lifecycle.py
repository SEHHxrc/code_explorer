# -*- coding: utf-8 -*-
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.services.projects import (
    ProjectDeletionError,
    ProjectDeletionService,
    ProjectRepository,
    ProjectUpdate,
)
from backend.app.services.projects.deletion_journal import ProjectDeletionJournal
from backend.app.services.projects.deletion_janitor import ProjectDeletionJanitor
from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspacePolicy
from backend.app.services.project_workspace.contracts import PreparedWorkspace, SanitizeReport
from backend.app.services.project_workspace.janitor import WorkspaceJanitor
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


class FakeProjects:
    def __init__(self):
        self.record = SimpleNamespace(project_id="project-1", user_id="user-1")

    def get_owned(self, project_id, user_id):
        return self.record if (project_id, user_id) == ("project-1", "user-1") else None

class RecoverableArtifacts:
    """模拟支持隔离、恢复和清理的项目产物仓储。"""

    def __init__(self):
        self.present = True
        self.quarantined = False

    def quarantine(self, project_id, operation_id):
        del project_id, operation_id
        self.present = False
        self.quarantined = True
        return True

    def restore(self, project_id, operation_id):
        del project_id, operation_id
        self.present = True
        self.quarantined = False

    def purge_quarantine(self, operation_id):
        del operation_id
        self.quarantined = False


class FakeDeletionRepository:
    """可配置活动任务和数据库提交失败的删除事务替身。"""

    def __init__(self, *, active=False, fail=False):
        self.active = active
        self.fail = fail
        self.deleted = False

    def has_active_tasks(self, project_id, user_id):
        del project_id, user_id
        return self.active

    def delete_owned_with_dependents(self, project_id, user_id):
        del project_id, user_id
        if self.fail:
            raise RuntimeError("database unavailable")
        self.deleted = True
        return True


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
            service = ProjectDeletionService(
                projects=FakeProjects(),
                deletion_repository=FakeDeletionRepository(active=True),
                artifacts=RecoverableArtifacts(),
                workspace=workspace,
                journal=ProjectDeletionJournal(Path(temp) / "deletion-journal"),
            )
            with self.assertRaises(ProjectDeletionError) as caught:
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
            deletions = FakeDeletionRepository()
            artifacts = RecoverableArtifacts()
            result = asyncio.run(ProjectDeletionService(
                projects=projects,
                deletion_repository=deletions,
                artifacts=artifacts,
                workspace=workspace,
                journal=ProjectDeletionJournal(Path(temp) / "deletion-journal"),
            ).delete("project-1", "user-1"))
            self.assertTrue(result.deleted)
            self.assertFalse(target.exists())
            self.assertFalse(artifacts.present)
            self.assertFalse(artifacts.quarantined)
            self.assertTrue(deletions.deleted)

    def test_database_failure_restores_quarantined_project_resources(self):
        """数据库删除失败时应恢复工作区、产物并移除补偿日志。"""
        with tempfile.TemporaryDirectory() as temp:
            workspace = self._workspace(temp)
            target = workspace.paths.project_root("user-1", "project-1")
            target.mkdir(parents=True)
            (target / "main.py").write_text("pass", encoding="utf-8")
            artifacts = RecoverableArtifacts()
            journal = ProjectDeletionJournal(Path(temp) / "deletion-journal")
            service = ProjectDeletionService(
                projects=FakeProjects(),
                deletion_repository=FakeDeletionRepository(fail=True),
                artifacts=artifacts,
                workspace=workspace,
                journal=journal,
            )
            with self.assertRaises(ProjectDeletionError):
                asyncio.run(service.delete("project-1", "user-1"))
            self.assertTrue(target.exists())
            self.assertTrue(artifacts.present)
            self.assertFalse(artifacts.quarantined)
            self.assertEqual([], journal.list_pending())

    def test_project_repository_updates_only_whitelisted_owned_fields(self):
        """项目更新应修改文件树，并保持身份、来源和路径不变。"""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        repository = ProjectRepository(sessions)
        created = repository.create(
            project_id="project-1",
            user_id="user-1",
            source="local_upload://demo.zip",
            local_path="/controlled/project-1",
            file_tree=[],
        )
        updated = repository.update_owned(
            "project-1",
            "user-1",
            ProjectUpdate(file_tree=[{"path": "main.py", "type": "file"}]),
        )
        self.assertIsNotNone(updated)
        self.assertEqual([{"path": "main.py", "type": "file"}], updated.file_tree)
        self.assertEqual(created.project_id, updated.project_id)
        self.assertEqual(created.user_id, updated.user_id)
        self.assertEqual(created.source, updated.source)
        self.assertEqual(created.local_path, updated.local_path)
        self.assertIsNone(repository.update_owned(
            "project-1",
            "another-user",
            ProjectUpdate(file_tree=[]),
        ))

    def test_deletion_janitor_restores_resources_when_database_record_exists(self):
        """进程中断后数据库记录仍存在时，应恢复删除隔离区中的资源。"""
        with tempfile.TemporaryDirectory() as temp:
            workspace = self._workspace(temp)
            target = workspace.paths.project_root("user-1", "project-1")
            target.mkdir(parents=True)
            (target / "main.py").write_text("pass", encoding="utf-8")
            artifacts = RecoverableArtifacts()
            journal = ProjectDeletionJournal(Path(temp) / "deletion-journal")
            operation = journal.begin("operation-1", "project-1", "user-1")
            workspace.filesystem.quarantine_project(
                "user-1",
                "project-1",
                operation.operation_id,
            )
            artifacts.quarantine("project-1", operation.operation_id)
            journal.transition(operation, "quarantined")

            result = ProjectDeletionJanitor(
                projects=FakeProjects(),
                artifacts=artifacts,
                workspace=workspace,
                journal=journal,
            ).cleanup_pending()

            self.assertEqual(1, result["restored"])
            self.assertTrue(target.exists())
            self.assertTrue(artifacts.present)
            self.assertEqual([], journal.list_pending())

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
