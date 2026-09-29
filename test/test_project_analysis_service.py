# -*- coding: utf-8 -*-
import asyncio
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.app.services.project_analysis import AnalyzeProjectCommand, ProjectSource
from backend.app.services.project_analysis.exceptions import ArtifactPersistenceError, DependencyAnalysisError, ProjectPersistenceError
from backend.app.services.project_analysis.service import ProjectAnalysisService
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.journal import OperationJournal
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


def project_zip():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    archive.seek(0)
    return archive


class RecordingArtifacts:
    def __init__(self, fail=False):
        self.saved = None
        self.removed = []
        self.fail = fail

    def save(self, project_id, payload):
        self.saved = (project_id, payload)
        if self.fail:
            raise RuntimeError("artifact unavailable")

    def remove(self, project_id):
        self.removed.append(project_id)


class RecordingProjects:
    def __init__(self, fail=False):
        self.created = None
        self.fail = fail

    def create(self, **values):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.created = values


class SuccessfulAnalyzer:
    def __init__(self, target_dir, max_workers):
        self.target_dir = target_dir

    def run_full_analysis(self):
        return {
            "file_symbols": {"main.py": [{"name": "app", "kind": "variable", "line": 2}]},
            "dependency_graph": {
                "nodes": [
                    {"id": "main.py", "name": "main.py", "level": "module", "file": "main.py"},
                    {"id": "main.py::app", "name": "app", "level": "variable", "file": "main.py", "line": 2},
                ],
                "links": [{"source": "main.py", "target": "main.py::app", "relation": "declares"}],
            },
            "stats": {"files_parsed": 1, "unresolved": 0},
            "diagnostics": {
                "unresolved_references": [],
                "coverage": {"total_files": 1, "parsed_files": 1},
            },
        }


class FailingAnalyzer:
    def __init__(self, target_dir, max_workers):
        pass

    def run_full_analysis(self):
        raise RuntimeError("parser crashed")

class FailingCompleteJournal(OperationJournal):
    def transition(self, operation, state):
        if state == "completed":
            raise OSError("journal unavailable")
        return super().transition(operation, state)

class ProjectAnalysisServiceTests(unittest.TestCase):
    def _service(self, root, projects, artifacts, analyzer):
        workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(root) / "users"))
        return ProjectAnalysisService(
            project_repository=projects,
            artifact_repository=artifacts,
            workspace_service=workspace,
            analyzer_factory=analyzer,
        )

    def _command(self):
        return AnalyzeProjectCommand(
            user_id="user-1",
            source=ProjectSource.zip(project_zip(), "sample.zip"),
        )

    def test_success_keeps_raw_artifact_and_publishes_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = RecordingArtifacts()
            projects = RecordingProjects()
            result = asyncio.run(self._service(
                temp, projects, artifacts, SuccessfulAnalyzer,
            ).analyze(self._command()))

            self.assertEqual(1, result.dependency_graph.summary.edge_count)
            self.assertIn("links", artifacts.saved[1]["dependency_graph"])
            self.assertIn("semantic_index", artifacts.saved[1])
            self.assertEqual(
                "1.1",
                artifacts.saved[1]["analysis_metadata"]["semantic_index_schema_version"],
            )
            self.assertEqual(1, artifacts.saved[1]["analysis_statistics"]["files_parsed"])
            self.assertEqual(1, artifacts.saved[1]["analysis_diagnostics"]["coverage"]["parsed_files"])
            self.assertEqual("2.0", artifacts.saved[1]["analysis_metadata"]["schema_version"])
            self.assertIn("security_evidence", artifacts.saved[1])
            self.assertTrue(artifacts.saved[1]["security_evidence"]["completed"])
            self.assertFalse(artifacts.saved[1]["security_evidence"]["dataflow_verified"])
            self.assertEqual(
                "2.3",
                artifacts.saved[1]["analysis_metadata"]["security_schema_version"],
            )
            self.assertEqual(
                "1.3",
                artifacts.saved[1]["analysis_metadata"]["security_ir_version"],
            )
            self.assertEqual(
                artifacts.saved[1]["security_evidence"]["rule_packs"],
                artifacts.saved[1]["analysis_metadata"]["security_rule_packs"],
            )
            self.assertEqual("local_upload://sample.zip", projects.created["source"])
            self.assertTrue(Path(projects.created["local_path"]).exists())
            self.assertFalse(any((Path(temp) / "users" / "user-1" / ".staging").glob("*")))

    def test_analyzer_failure_removes_staging_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            projects = RecordingProjects()
            with self.assertRaises(DependencyAnalysisError):
                asyncio.run(self._service(
                    temp, projects, RecordingArtifacts(), FailingAnalyzer,
                ).analyze(self._command()))
            self.assertIsNone(projects.created)
            self.assertFalse(any((Path(temp) / "users").rglob("main.py")))

    def test_artifact_failure_removes_published_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = RecordingArtifacts(fail=True)
            with self.assertRaises(ArtifactPersistenceError):
                asyncio.run(self._service(
                    temp, RecordingProjects(), artifacts, SuccessfulAnalyzer,
                ).analyze(self._command()))
            self.assertEqual(1, len(artifacts.removed))
            self.assertFalse(any((Path(temp) / "users").rglob("main.py")))

    def test_repository_failure_removes_artifact_and_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = RecordingArtifacts()
            with self.assertRaises(ProjectPersistenceError):
                asyncio.run(self._service(
                    temp, RecordingProjects(fail=True), artifacts, SuccessfulAnalyzer,
                ).analyze(self._command()))
            self.assertEqual(1, len(artifacts.removed))
            self.assertFalse(any((Path(temp) / "users").rglob("main.py")))

    def test_completed_database_commit_is_not_rolled_back_when_journal_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = ProjectWorkspacePaths(Path(temp) / "users")
            workspace = ProjectWorkspaceService(paths=paths, journal=FailingCompleteJournal())
            projects = RecordingProjects()
            result = asyncio.run(ProjectAnalysisService(
                project_repository=projects,
                artifact_repository=RecordingArtifacts(),
                workspace_service=workspace,
                analyzer_factory=SuccessfulAnalyzer,
            ).analyze(self._command()))
            self.assertEqual(result.project_id, projects.created["project_id"])
            self.assertTrue(Path(projects.created["local_path"]).exists())

if __name__ == "__main__":
    unittest.main()
