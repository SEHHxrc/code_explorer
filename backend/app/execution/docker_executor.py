"""使用 Docker CLI 执行单个受策略约束的短生命周期任务。"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .contracts import ExecutionPlan
from .policy import ExecutionSettings


@dataclass(frozen=True)
class DockerExecutionResult:
    """容器执行终态。"""

    status: str
    exit_code: int | None
    error: str | None
    output_truncated: bool


class DockerExecutor:
    """只用参数数组调用 Docker；绝不通过宿主 Shell 拼接命令。"""

    def __init__(self, settings: ExecutionSettings):
        self.settings = settings

    def build_command(self, plan: ExecutionPlan, project_root: Path, container_name: str) -> list[str]:
        """生成包含不可绕过隔离参数的 Docker CLI argv。"""
        mount_source = str(project_root.resolve())
        if "," in mount_source:
            raise ValueError("Workspace path cannot contain a comma for Docker mount syntax")
        return [
            self.settings.docker_binary,
            "run",
            "--pull", "never",
            "--name", container_name,
            "--rm",
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--user", "65532:65532",
            "--pids-limit", str(plan.pids_limit),
            "--memory", f"{plan.memory_mb}m",
            "--cpus", str(plan.cpu_limit),
            "--workdir", "/workspace",
            "--env", "HOME=/tmp",
            "--mount", f"type=bind,source={mount_source},target=/workspace,readonly",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--label", "code-explorer.execution=true",
            plan.image,
            *plan.argv,
        ]

    def execute(
        self,
        *,
        task_id: str,
        plan: ExecutionPlan,
        project_root: Path,
        cancelled: Callable[[], bool],
        on_output: Callable[[str], None],
        heartbeat: Callable[[], None] = lambda: None,
    ) -> DockerExecutionResult:
        """运行容器、流式截断输出，并响应数据库取消和超时。"""
        container_name = f"code-explorer-{task_id[:32]}"
        command = self.build_command(plan, project_root, container_name)
        try:
            process = subprocess.Popen(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError:
            return DockerExecutionResult("failed", None, "Docker executable is unavailable on the worker.", False)
        except OSError:
            return DockerExecutionResult("failed", None, "Docker process could not be started.", False)

        chunks: queue.Queue[bytes | None] = queue.Queue()

        def read_output() -> None:
            stream = process.stdout
            if stream is not None:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    chunks.put(chunk)
            chunks.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + plan.timeout_seconds
        output_bytes = 0
        truncated = False
        reader_finished = False
        forced_status = None
        forced_at = None
        last_heartbeat = time.monotonic()

        try:
            while process.poll() is None or not reader_finished:
                while True:
                    try:
                        chunk = chunks.get_nowait()
                    except queue.Empty:
                        break
                    if chunk is None:
                        reader_finished = True
                        break
                    remaining = self.settings.max_output_bytes - output_bytes
                    if remaining > 0:
                        accepted = chunk[:remaining]
                        output_bytes += len(accepted)
                        on_output(accepted.decode("utf-8", errors="replace"))
                    if len(chunk) > max(remaining, 0):
                        truncated = True

                now = time.monotonic()
                if now - last_heartbeat >= 5:
                    heartbeat()
                    last_heartbeat = now
                if process.poll() is None:
                    if forced_status and forced_at is not None and now - forced_at >= 5:
                        process.kill()
                    elif cancelled():
                        forced_status = "cancelled"
                        forced_at = now
                        self._force_remove(container_name)
                    elif now >= deadline:
                        forced_status = "timed_out"
                        forced_at = now
                        self._force_remove(container_name)
                if process.poll() is None or not reader_finished:
                    time.sleep(0.1)
        finally:
            if process.poll() is None:
                self._force_remove(container_name)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            reader.join(timeout=1)

        exit_code = process.wait()
        if forced_status:
            return DockerExecutionResult(forced_status, exit_code, None, truncated)
        if exit_code == 0:
            return DockerExecutionResult("completed", exit_code, None, truncated)
        return DockerExecutionResult("failed", exit_code, "Container command exited with a non-zero status.", truncated)

    def _force_remove(self, container_name: str) -> None:
        """强制删除仅由本 Worker 命名的容器；忽略清理命令自身输出。"""
        try:
            subprocess.run(
                [self.settings.docker_binary, "rm", "-f", container_name],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
