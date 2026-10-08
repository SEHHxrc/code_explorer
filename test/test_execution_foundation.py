# -*- coding: utf-8 -*-
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.execution.contracts import ExecutionTaskRequest
from backend.app.execution.docker_executor import DockerExecutionResult, DockerExecutor
from backend.app.execution.policy import ExecutionError, ExecutionPolicy, ExecutionSettings
from backend.app.execution.repository import ExecutionRepository
from backend.app.execution.service import ExecutionService
from backend.app.execution.worker import ExecutionWorker
from backend.app.models import Base, ProjectModel
from backend.app.services.projects import ProjectDeletionRepository
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


def settings():
    return ExecutionSettings(
        allowed_images=frozenset({"python:3.12-alpine"}),
        scan_images={"bandit": "bandit:local"},
        max_timeout_seconds=180,
        max_cpu=2,
        max_memory_mb=1024,
        max_pids=128,
        max_output_bytes=4096,
        docker_binary="docker",
    )


def command_request():
    return ExecutionTaskRequest(
        kind="command",
        image="python:3.12-alpine",
        argv=["python", "-m", "compileall", "-q", "."],
        timeout_seconds=60,
        cpu_limit=1,
        memory_mb=256,
        pids_limit=64,
    )


class FakeExecutor:
    def execute(self, *, on_output, heartbeat, **kwargs):
        heartbeat()
        on_output("safe output")
        return DockerExecutionResult("completed", 0, None, False)


class ExecutionFoundationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        self.repository = ExecutionRepository(self.sessions)

    def test_policy_is_allowlist_based_and_selects_scan_command(self):
        policy = ExecutionPolicy(settings())
        plan = policy.resolve(command_request())
        self.assertEqual(plan.image, "python:3.12-alpine")
        self.assertEqual(plan.argv[0], "python")
        scan = policy.resolve(ExecutionTaskRequest(
            kind="security_scan",
            scan_profile="bandit",
            timeout_seconds=60,
            cpu_limit=1,
            memory_mb=256,
            pids_limit=64,
        ))
        self.assertEqual(scan.image, "bandit:local")
        self.assertEqual(scan.argv[:2], ["bandit", "-r"])
        with self.assertRaises(ExecutionError):
            policy.resolve(ExecutionTaskRequest(
                kind="command",
                image="untrusted:latest",
                argv=["echo", "no"],
            ))

    def test_docker_command_has_isolation_and_no_shell_string(self):
        plan = ExecutionPolicy(settings()).resolve(command_request())
        with tempfile.TemporaryDirectory() as directory:
            command = DockerExecutor(settings()).build_command(
                plan, Path(directory), "code-explorer-test",
            )
        self.assertIsInstance(command, list)
        self.assertIn("--network", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertIn("no-new-privileges:true", command)
        self.assertIn("readonly", command[command.index("--mount") + 1])
        self.assertEqual(command[-5:], ["python", "-m", "compileall", "-q", "."])

    def test_executor_cleans_container_when_audit_callback_fails(self):
        class Stream:
            def __init__(self):
                self.chunks = [b"output", b""]

            def read(self, size):
                return self.chunks.pop(0)

        class Process:
            def __init__(self):
                self.stdout = Stream()
                self.returncode = None
                self.killed = False

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                if self.returncode is None:
                    raise RuntimeError("process was not stopped")
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = Process()
        executor = DockerExecutor(settings())
        executor._force_remove = lambda name: process.kill()
        plan = ExecutionPolicy(settings()).resolve(command_request())
        with tempfile.TemporaryDirectory() as directory:
            with patch("backend.app.execution.docker_executor.subprocess.Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                    executor.execute(
                        task_id="task1",
                        plan=plan,
                        project_root=Path(directory),
                        cancelled=lambda: False,
                        on_output=lambda text: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
                    )
        self.assertTrue(process.killed)

    def test_service_rejects_when_user_queue_quota_is_full(self):
        limited = replace(settings(), max_active_tasks_per_user=1)
        policy = ExecutionPolicy(limited)
        self.repository.create(
            task_id="task1",
            project_id="project1",
            user_id="user1",
            plan=policy.resolve(command_request()),
        )

        class Projects:
            def get_owned(self, project_id, user_id):
                return object()

        service = ExecutionService(
            repository=self.repository,
            projects=Projects(),
            policy=policy,
        )
        with self.assertRaises(ExecutionError) as captured:
            service.submit("project1", "user1", command_request())
        self.assertEqual(captured.exception.status_code, 429)

    def test_repository_claim_cancel_and_event_cursor(self):
        plan = ExecutionPolicy(settings()).resolve(command_request())
        first = self.repository.create(
            task_id="task1", project_id="project1", user_id="user1", plan=plan,
        )
        self.assertNotIn("user_id", first.model_dump())
        claimed = self.repository.claim_next("worker1")
        self.assertEqual(claimed.status, "running")
        self.repository.add_event("task1", "task.output", {"text": "ok"})
        events = self.repository.events_after("task1", 0)
        self.assertEqual([item.type for item in events], ["task.queued", "task.started", "task.output"])
        self.assertEqual([item.sequence for item in events], sorted({item.sequence for item in events}))

        self.repository.create(
            task_id="task2", project_id="project1", user_id="user1", plan=plan,
        )
        cancelled = self.repository.request_cancel("task2", "user1")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(self.repository.claim_next("worker1"))

    def test_project_deletion_repository_detects_and_cleans_execution_tasks(self):
        deletions = ProjectDeletionRepository(self.sessions)
        db = self.sessions()
        db.add(ProjectModel(
            id="project1",
            user_id="user1",
            repo_url="upload.zip",
            local_path="controlled",
            file_tree=[],
        ))
        db.commit()
        db.close()
        plan = ExecutionPolicy(settings()).resolve(command_request())
        self.repository.create(
            task_id="task1", project_id="project1", user_id="user1", plan=plan,
        )
        self.assertTrue(deletions.has_active_tasks("project1", "user1"))
        self.repository.request_cancel("task1", "user1")
        self.assertFalse(deletions.has_active_tasks("project1", "user1"))
        self.assertTrue(deletions.delete_owned_with_dependents("project1", "user1"))
        self.assertIsNone(self.repository.get("task1", "user1"))

    def test_worker_uses_controlled_workspace_and_persists_terminal_state(self):
        plan = ExecutionPolicy(settings()).resolve(command_request())
        self.repository.create(
            task_id="task1", project_id="project1", user_id="user1", plan=plan,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectWorkspacePaths(Path(directory))
            paths.project_root("user1", "project1").mkdir(parents=True)
            worker = ExecutionWorker(
                worker_id="worker1",
                repository=self.repository,
                settings=settings(),
                workspace_paths=paths,
            )
            worker.executor = FakeExecutor()
            self.assertTrue(worker.run_once())
        view = self.repository.get("task1", "user1")
        self.assertEqual(view.status, "completed")
        self.assertEqual(view.exit_code, 0)
        event_types = [item.type for item in self.repository.events_after("task1", 0)]
        self.assertIn("task.output", event_types)
        self.assertIn("task.completed", event_types)


if __name__ == "__main__":
    unittest.main()
