"""可作为独立进程启动的持久化队列 Docker Worker。"""

from __future__ import annotations

import argparse
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.models import init_db
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths

from .contracts import ExecutionPlan
from .docker_executor import DockerExecutor
from .policy import ExecutionError, ExecutionPolicy, ExecutionSettings
from .repository import ExecutionRepository


class ExecutionWorker:
    """认领单个队列任务，在受限容器中执行并持久化完整状态转换。"""

    def __init__(
        self,
        *,
        worker_id: str,
        repository: ExecutionRepository | None = None,
        settings: ExecutionSettings | None = None,
        workspace_paths: ProjectWorkspacePaths | None = None,
    ):
        self.worker_id = worker_id
        self.repository = repository or ExecutionRepository()
        self.settings = settings or ExecutionSettings.from_env()
        self.policy = ExecutionPolicy(self.settings)
        self.paths = workspace_paths or ProjectWorkspacePaths()
        self.executor = DockerExecutor(self.settings)

    def recover_stale(self, lease_seconds: int = 30) -> list[str]:
        """终结失去心跳的任务；不自动重试以避免重复执行命令。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(lease_seconds, 15))
        return self.repository.recover_stale(cutoff)

    def run_once(self) -> bool:
        """认领并执行一个任务；没有任务时返回 False。"""
        task = self.repository.claim_next(self.worker_id)
        if task is None:
            return False
        plan = ExecutionPlan(
            kind=task.kind,
            image=task.image,
            argv=task.argv,
            scan_profile=task.scan_profile,
            timeout_seconds=task.timeout_seconds,
            cpu_limit=task.cpu_limit,
            memory_mb=task.memory_mb,
            pids_limit=task.pids_limit,
        )
        try:
            self.policy.validate_plan(plan)
            project_root = self.paths.project_root(task.user_id, task.project_id)
            self._validate_workspace(project_root)
            result = self.executor.execute(
                task_id=task.id,
                plan=plan,
                project_root=project_root,
                cancelled=lambda: self.repository.is_cancel_requested(task.id),
                heartbeat=lambda: self.repository.heartbeat(task.id, self.worker_id),
                on_output=lambda text: self._append_output(task.id, text),
            )
            self.repository.finish(
                task.id,
                status=result.status,
                exit_code=result.exit_code,
                error=result.error,
                output_truncated=result.output_truncated,
            )
        except ExecutionError as exc:
            self.repository.finish(task.id, status="failed", exit_code=None, error=exc.public_message)
        except Exception:
            self.repository.finish(
                task.id,
                status="failed",
                exit_code=None,
                error="Execution worker failed before the container completed.",
            )
        return True

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        """持续消费队列；Worker 进程应由系统服务或容器编排器监督。"""
        self.recover_stale()
        while True:
            if not self.run_once():
                time.sleep(max(poll_seconds, 0.1))

    def _validate_workspace(self, project_root: Path) -> None:
        """只接受由受控用户/项目 ID 计算出的真实目录，拒绝符号链接。"""
        if not project_root.exists() or not project_root.is_dir() or project_root.is_symlink():
            raise ExecutionError("Controlled project workspace is unavailable.", 409)

    def _append_output(self, task_id: str, text: str) -> None:
        """保存有界输出块；总字节限制由 DockerExecutor 强制执行。"""
        if text:
            self.repository.add_event(task_id, "task.output", {"text": text})


def default_worker_id() -> str:
    """生成稳定且满足审计可读性的默认 Worker ID。"""
    raw = os.getenv("EXECUTION_WORKER_ID", "").strip()
    if raw:
        return raw[:64]
    host = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in socket.gethostname())
    return (host or "worker")[:64]


def main() -> None:
    parser = argparse.ArgumentParser(description="Code Explorer isolated execution worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued task")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    arguments = parser.parse_args()
    init_db()
    worker = ExecutionWorker(worker_id=default_worker_id())
    worker.recover_stale()
    if arguments.once:
        worker.run_once()
    else:
        worker.run_forever(arguments.poll_seconds)


if __name__ == "__main__":
    main()
