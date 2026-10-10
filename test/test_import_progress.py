"""进度所有权、实时计数、导入事务终态及并发 HTTP 查询测试。"""

import asyncio
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services.project_analysis.progress import report_progress
from backend.app.services.projects import AnalyzeProjectCommand, ProjectAnalysisError, ProjectSource
from backend.app.services.projects.import_service import ProjectImportService
from backend.app.services.projects.progress import ImportProgressStore
from test import test_project_analysis_service as import_fixtures


class ImportProgressTests(unittest.TestCase):
    def test_original_analyzer_progress_reader_reports_real_file_attempts(self):
        from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
        store = ImportProgressStore()
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "a.py").write_bytes(b"def a():\n    return 1\n")
            (Path(directory) / "b.py").write_bytes(b"def b():\n    return 2\n")
            analyzer = UnifiedCodeAnalyzer(directory, max_workers=1)
            tracker.phase("dependency", analyzer.get_progress)
            analyze_file = analyzer._analyze_file
            attempted = []
            def observe_file(path):
                context = analyze_file(path)
                attempted.append(store.snapshot(request_id, "alice")["processed_files"])
                return context
            with patch.object(analyzer, "_analyze_file", side_effect=observe_file):
                analyzer.run_full_analysis()
            self.assertEqual([1, 2], attempted)
            snapshot = store.snapshot(request_id, "alice")
            self.assertEqual(2, snapshot["total_files"])
            self.assertEqual("running", snapshot["status"])
            self.assertEqual("生成依赖图和语义索引产物", snapshot["detail"])

    def test_ownership_and_replay_protection(self):
        store = ImportProgressStore()
        request_id = store.create("alice")
        for action in (store.snapshot, store.claim):
            with self.assertRaises(ProjectAnalysisError) as denied:
                action(request_id, "bob")
            self.assertEqual(404, denied.exception.status_code)
        tracker = store.claim(request_id, "alice")
        with self.assertRaises(ProjectAnalysisError) as replay:
            store.claim(request_id, "alice")
        self.assertEqual(409, replay.exception.status_code)
        tracker.complete("project-1")
        self.assertEqual("completed", store.snapshot(request_id, "alice")["status"])

    def test_live_reader_is_reused_and_released_when_phase_changes(self):
        store = ImportProgressStore()
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        counts = {"total_files": 20, "parsed_files": 3, "stage_label": "解析源码"}
        tracker.phase("dependency", lambda: dict(counts))
        self.assertEqual(3, store.snapshot(request_id, "alice")["processed_files"])
        counts["parsed_files"] = 20
        self.assertEqual("running", store.snapshot(request_id, "alice")["status"])
        self.assertEqual(20, store.snapshot(request_id, "alice")["processed_files"])
        tracker.phase("dataflow")
        self.assertEqual(0, store.snapshot(request_id, "alice")["total_files"])
        self.assertIsNone(store._entries[request_id].reader)
        tracker.complete("project-1")
        tracker.phase("dependency", lambda: counts)
        self.assertEqual("completed", store.snapshot(request_id, "alice")["stage"])

    def test_pending_keepalive_terminal_expiry_and_active_task_retention(self):
        now = [0.0]
        store = ImportProgressStore(ttl_seconds=10, clock=lambda: now[0])
        request_id = store.create("alice")
        now[0] = 9
        store.snapshot(request_id, "alice")
        now[0] = 18
        tracker = store.claim(request_id, "alice")
        now[0] = 100
        self.assertEqual("running", store.snapshot(request_id, "alice")["status"])
        tracker.fail("公开的失败信息")
        now[0] = 109
        self.assertEqual("failed", store.snapshot(request_id, "alice")["status"])
        now[0] = 110
        with self.assertRaises(ProjectAnalysisError):
            store.snapshot(request_id, "alice")

    def test_capacity_and_per_user_limits(self):
        store = ImportProgressStore(max_entries=2, max_per_user=1)
        request_id = store.create("alice")
        with self.assertRaises(ProjectAnalysisError) as limited:
            store.create("alice")
        self.assertEqual(429, limited.exception.status_code)
        store.claim(request_id, "alice").complete("project-1")
        store.create("bob")
        with self.assertRaises(ProjectAnalysisError):
            store.create("charlie")

    def test_progress_reader_and_observer_failure_do_not_break_analysis(self):
        store = ImportProgressStore()
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        tracker.phase("dependency", lambda: {"total_files": "bad"})
        self.assertEqual(0, store.snapshot(request_id, "alice")["total_files"])
        class BrokenObserver:
            def phase(self, *_):
                raise RuntimeError("observer failed")
        with self.assertLogs("backend.app.services.project_analysis.progress", level="WARNING"):
            report_progress(BrokenObserver(), "dependency")

    def test_import_terminal_status_follows_transaction_result(self):
        store = ImportProgressStore()
        service = ProjectImportService()
        command = AnalyzeProjectCommand(user_id="alice", source=ProjectSource.git("https://example.test/repo"))
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        with patch.object(service, "_import_sync", return_value=SimpleNamespace(project_id="project-1")):
            asyncio.run(service.import_project(command, progress=tracker))
        self.assertEqual("project-1", store.snapshot(request_id, "alice")["project_id"])
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        with patch.object(service, "_import_sync", side_effect=ProjectAnalysisError("安全错误", status_code=422)):
            with self.assertRaises(ProjectAnalysisError):
                asyncio.run(service.import_project(command, progress=tracker))
        snapshot = store.snapshot(request_id, "alice")
        self.assertEqual("failed", snapshot["status"])
        self.assertEqual("安全错误", snapshot["message"])

    def test_real_pipeline_reports_phases_and_finishes_after_publication(self):
        store = ImportProgressStore()
        request_id = store.create("user-1")
        tracker = store.claim(request_id, "user-1")
        stages = []
        phase = tracker.phase

        def record_phase(stage, reader=None):
            stages.append(stage)
            phase(stage, reader)

        with tempfile.TemporaryDirectory() as directory:
            projects = import_fixtures.RecordingProjects()
            artifacts = import_fixtures.RecordingArtifacts()
            helper = import_fixtures.ProjectImportServiceTests()
            service = helper._service(directory, projects, artifacts, import_fixtures.SuccessfulAnalyzer)
            with patch.object(tracker, "phase", side_effect=record_phase):
                result = asyncio.run(service.import_project(helper._command(), progress=tracker))
            self.assertEqual([
                "preparing", "dependency", "security_scan", "security_scan", "program_graph",
                "dataflow", "evidence", "projection", "publishing", "persisting",
            ], stages)
            self.assertEqual(result.project_id, projects.created["project_id"])
            self.assertEqual("completed", store.snapshot(request_id, "user-1")["status"])

    def test_real_artifact_failure_keeps_rollback_and_reports_failed(self):
        store = ImportProgressStore()
        request_id = store.create("user-1")
        tracker = store.claim(request_id, "user-1")
        with tempfile.TemporaryDirectory() as directory:
            artifacts = import_fixtures.RecordingArtifacts(fail=True)
            projects = import_fixtures.RecordingProjects()
            helper = import_fixtures.ProjectImportServiceTests()
            service = helper._service(directory, projects, artifacts, import_fixtures.SuccessfulAnalyzer)
            with self.assertRaises(ProjectAnalysisError):
                asyncio.run(service.import_project(helper._command(), progress=tracker))
            self.assertEqual(1, len(artifacts.removed))
            self.assertIsNone(projects.created)
            self.assertEqual("failed", store.snapshot(request_id, "user-1")["status"])
            self.assertIsNone(store._entries[request_id].reader)


class ImportProgressHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_browser_wait_does_not_cancel_thread_terminal_update(self):
        store = ImportProgressStore()
        request_id = store.create("alice")
        tracker = store.claim(request_id, "alice")
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        service = ProjectImportService()
        command = AnalyzeProjectCommand(user_id="alice", source=ProjectSource.git("https://example.test/repo"))

        def slow_import(*_, **__):
            started.set()
            if not release.wait(5):
                raise AssertionError("test import was not released")
            return SimpleNamespace(project_id="project-1")

        complete = tracker.complete
        def record_complete(project_id):
            complete(project_id)
            finished.set()

        with patch.object(service, "_import_sync", side_effect=slow_import), \
             patch.object(tracker, "complete", side_effect=record_complete):
            task = asyncio.create_task(service.import_project(command, progress=tracker))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual("running", store.snapshot(request_id, "alice")["status"])
            finally:
                release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 3))
            self.assertEqual("completed", store.snapshot(request_id, "alice")["status"])

    async def test_progress_can_be_read_while_analysis_post_is_running(self):
        import httpx
        from fastapi import FastAPI
        from backend.app.api import project
        app = FastAPI()
        app.include_router(project.router)
        started = threading.Event()
        release = threading.Event()
        counts = {"total_files": 100, "parsed_files": 1}
        service = ProjectImportService()

        def slow_import(*_, progress=None):
            progress.phase("dependency", lambda: dict(counts))
            started.set()
            if not release.wait(5):
                raise AssertionError("test import was not released")
            raise ProjectAnalysisError("测试分析失败，已回滚。", status_code=422)

        with patch.object(project, "import_progress", ImportProgressStore()), \
             patch.object(project, "project_import_service", service), \
             patch.object(service, "_import_sync", side_effect=slow_import):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                created = await client.post("/api/projects/analysis-progress", headers={"X-User-Id": "alice"})
                request_id = created.json()["data"]["request_id"]
                task = asyncio.create_task(client.post(
                    "/api/projects/analyze", data={"repo_url": "https://example.test/repo", "request_id": request_id},
                    headers={"X-User-Id": "alice"},
                ))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 3))
                    response = await client.get(f"/api/projects/analysis-progress/{request_id}", headers={"X-User-Id": "bob"})
                    self.assertEqual(404, response.status_code)
                    response = await client.get(f"/api/projects/analysis-progress/{request_id}", headers={"X-User-Id": "alice"})
                    self.assertEqual(1, response.json()["data"]["processed_files"])
                    counts["parsed_files"] = 50
                    response = await client.get(f"/api/projects/analysis-progress/{request_id}", headers={"X-User-Id": "alice"})
                    self.assertEqual(50, response.json()["data"]["processed_files"])
                    self.assertFalse(task.done())
                finally:
                    release.set()
                    response = await task
                self.assertEqual(422, response.status_code)
                response = await client.get(f"/api/projects/analysis-progress/{request_id}", headers={"X-User-Id": "alice"})
                self.assertEqual("failed", response.json()["data"]["status"])
