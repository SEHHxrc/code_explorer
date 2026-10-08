# -*- coding: utf-8 -*-
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app.services.projects.artifacts import ProjectArtifactRepository
from backend.app.services.projects.transaction import ProjectImportTransaction
from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspacePolicy, WorkspaceSource
from backend.app.services.project_workspace.exceptions import SourceValidationError, WorkspacePolicyError
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths
from backend.app.services.project_workspace.sources.git import validate_repo_url


class NoopArtifacts(ProjectArtifactRepository):
    def remove(self, project_id):
        return None


def zip_bytes(entries):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in entries.items():
            output.writestr(name, content)
    archive.seek(0)
    return archive


class WorkspaceSecurityTests(unittest.TestCase):
    def test_repo_url_rejects_local_non_http_and_private_dns(self):
        policy = WorkspacePolicy()
        with self.assertRaises(SourceValidationError):
            validate_repo_url("file:///etc/passwd", policy)
        with patch("backend.app.services.project_workspace.sources.git.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]), self.assertRaises(SourceValidationError):
            validate_repo_url("https://example.test/repo.git", policy)

    def test_repo_url_honors_configured_host_allowlist(self):
        policy = WorkspacePolicy(allowed_git_hosts=frozenset({"github.com"}))
        with self.assertRaises(SourceValidationError):
            validate_repo_url("https://gitlab.com/example/repo.git", policy)

    def test_zip_traversal_rolls_back_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            transaction = ProjectImportTransaction(workspace, NoopArtifacts())
            with self.assertRaises(WorkspacePolicyError):
                with transaction:
                    operation = transaction.begin("test_user")
                    workspace.prepare(operation, WorkspaceSource.zip(
                        zip_bytes({"../outside.py": "print('unsafe')"}), "unsafe.zip"
                    ))
            self.assertFalse(any(Path(directory).rglob("outside.py")))
            self.assertFalse(any((Path(directory) / "users" / "test_user" / ".staging").glob("*")))

    def test_zip_extracts_and_sanitizes_regular_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            transaction = ProjectImportTransaction(workspace, NoopArtifacts())
            with transaction:
                operation = transaction.begin("test_user")
                prepared = workspace.prepare(operation, WorkspaceSource.zip(zip_bytes({
                    "src/main.py": "print('ok')",
                    ".env.local": "TOKEN=secret",
                }), "sample.zip"))
                self.assertEqual("print('ok')", (operation.source_root / "src" / "main.py").read_text())
                self.assertFalse((operation.source_root / ".env.local").exists())
                self.assertEqual(1, prepared.sanitize_report.removed_sensitive_files)
            self.assertFalse(operation.operation_root.exists())


if __name__ == "__main__":
    unittest.main()
