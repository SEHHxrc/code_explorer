"""执行镜像、命令形状和资源配额的失败关闭策略。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .contracts import ExecutionError, ExecutionPlan, ExecutionTaskRequest


_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
_SCAN_COMMANDS = {
    "bandit": ["bandit", "-r", "/workspace", "-f", "json"],
    "semgrep": ["semgrep", "scan", "--config", "/rules", "--json", "/workspace"],
}


@dataclass(frozen=True)
class ExecutionSettings:
    """从服务环境读取的 Worker 安全上限和镜像白名单。"""

    allowed_images: frozenset[str]
    scan_images: dict[str, str]
    max_timeout_seconds: int = 300
    max_cpu: float = 2.0
    max_memory_mb: int = 2048
    max_pids: int = 256
    max_output_bytes: int = 1_048_576
    max_active_tasks_per_user: int = 5
    docker_binary: str = "docker"

    @classmethod
    def from_env(cls) -> "ExecutionSettings":
        """读取环境变量；未配置镜像时保持禁用，而不是提供宽松默认值。"""
        allowed = frozenset(
            item.strip() for item in os.getenv("EXECUTION_ALLOWED_IMAGES", "").split(",") if item.strip()
        )
        scans = {
            name: image
            for name, image in {
                "bandit": os.getenv("EXECUTION_SCAN_IMAGE_BANDIT", "").strip(),
                "semgrep": os.getenv("EXECUTION_SCAN_IMAGE_SEMGREP", "").strip(),
            }.items()
            if image
        }
        return cls(
            allowed_images=allowed,
            scan_images=scans,
            max_timeout_seconds=int(os.getenv("EXECUTION_MAX_TIMEOUT_SECONDS", "300")),
            max_cpu=float(os.getenv("EXECUTION_MAX_CPUS", "2")),
            max_memory_mb=int(os.getenv("EXECUTION_MAX_MEMORY_MB", "2048")),
            max_pids=int(os.getenv("EXECUTION_MAX_PIDS", "256")),
            max_output_bytes=int(os.getenv("EXECUTION_MAX_OUTPUT_BYTES", "1048576")),
            max_active_tasks_per_user=int(os.getenv("EXECUTION_MAX_ACTIVE_TASKS_PER_USER", "5")),
            docker_binary=os.getenv("EXECUTION_DOCKER_BINARY", "docker").strip() or "docker",
        )

    @property
    def configured(self) -> bool:
        return bool(self.allowed_images or self.scan_images)


class ExecutionPolicy:
    """把用户请求解析成满足服务端上限的容器执行计划。"""

    def __init__(self, settings: ExecutionSettings | None = None):
        self.settings = settings or ExecutionSettings.from_env()

    def resolve(self, request: ExecutionTaskRequest) -> ExecutionPlan:
        """验证镜像、参数与资源上限；失败时不创建任务。"""
        if request.kind == "security_scan":
            image = self.settings.scan_images.get(request.scan_profile or "")
            if not image:
                raise ExecutionError("Requested security scan profile is not configured.", 409)
            argv = list(_SCAN_COMMANDS[request.scan_profile])
        else:
            image = request.image or ""
            if image not in self.settings.allowed_images:
                raise ExecutionError("Container image is not in the execution allowlist.", 403)
            argv = list(request.argv)

        if not _IMAGE_PATTERN.fullmatch(image):
            raise ExecutionError("Configured container image is invalid.", 500)
        self._validate_argv(argv)
        limits = (
            (request.timeout_seconds, self.settings.max_timeout_seconds, "timeout"),
            (request.cpu_limit, self.settings.max_cpu, "CPU"),
            (request.memory_mb, self.settings.max_memory_mb, "memory"),
            (request.pids_limit, self.settings.max_pids, "PID"),
        )
        for requested, maximum, label in limits:
            if requested > maximum:
                raise ExecutionError(f"Requested {label} limit exceeds server policy.", 422)
        return ExecutionPlan(
            kind=request.kind,
            image=image,
            argv=argv,
            scan_profile=request.scan_profile,
            timeout_seconds=request.timeout_seconds,
            cpu_limit=request.cpu_limit,
            memory_mb=request.memory_mb,
            pids_limit=request.pids_limit,
        )

    def validate_plan(self, plan: ExecutionPlan) -> None:
        """Worker 执行前复核持久化计划，应用当前而非入队时的白名单。"""
        allowed = set(self.settings.allowed_images) | set(self.settings.scan_images.values())
        if plan.image not in allowed:
            raise ExecutionError("Queued container image is no longer allowed.", 403)
        self._validate_argv(plan.argv)
        limits = (
            (plan.timeout_seconds, self.settings.max_timeout_seconds),
            (plan.cpu_limit, self.settings.max_cpu),
            (plan.memory_mb, self.settings.max_memory_mb),
            (plan.pids_limit, self.settings.max_pids),
        )
        if any(requested > maximum for requested, maximum in limits):
            raise ExecutionError("Queued task exceeds current server resource policy.", 422)

    @staticmethod
    def _validate_argv(argv: list[str]) -> None:
        """限制参数数量和长度；Worker 始终以 shell=False 使用该数组。"""
        if not argv or len(argv) > 64:
            raise ExecutionError("Execution argv must contain between 1 and 64 items.", 422)
        total = 0
        for argument in argv:
            if not isinstance(argument, str) or not argument or "\x00" in argument:
                raise ExecutionError("Execution argv contains an invalid item.", 422)
            encoded = len(argument.encode("utf-8"))
            if encoded > 2048:
                raise ExecutionError("Execution argument is too long.", 422)
            total += encoded
        if total > 8192:
            raise ExecutionError("Execution argv exceeds the total size limit.", 422)
