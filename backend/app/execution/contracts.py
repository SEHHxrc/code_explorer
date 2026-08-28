"""隔离执行任务的输入、输出和内部执行计划契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ExecutionKind = Literal["command", "security_scan"]
ExecutionStatus = Literal[
    "queued", "running", "cancel_requested", "completed", "failed", "cancelled", "timed_out"
]
TERMINAL_EXECUTION_STATUSES = {"completed", "failed", "cancelled", "timed_out"}


class ExecutionTaskRequest(BaseModel):
    """提交到隔离 Worker 的声明式请求；不接受宿主机 Shell 字符串。"""

    kind: ExecutionKind
    image: str | None = Field(default=None, min_length=1, max_length=255)
    argv: list[str] = Field(default_factory=list, max_length=64)
    scan_profile: Literal["bandit", "semgrep"] | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    cpu_limit: float = Field(default=1.0, gt=0, le=4.0)
    memory_mb: int = Field(default=512, ge=64, le=4096)
    pids_limit: int = Field(default=128, ge=16, le=512)

    @model_validator(mode="after")
    def validate_shape(self):
        """按任务类型校验互斥字段，输出形状明确的请求。"""
        if self.kind == "command":
            if not self.image or not self.argv:
                raise ValueError("command tasks require image and argv")
            if self.scan_profile:
                raise ValueError("command tasks cannot set scan_profile")
        else:
            if not self.scan_profile:
                raise ValueError("security_scan tasks require scan_profile")
            if self.image or self.argv:
                raise ValueError("security_scan image and argv are selected by server policy")
        return self


class ExecutionPlan(BaseModel):
    """策略验证后持久化的不可变执行计划。"""

    kind: ExecutionKind
    image: str
    argv: list[str]
    scan_profile: str | None = None
    timeout_seconds: int
    cpu_limit: float
    memory_mb: int
    pids_limit: int


class ExecutionTaskView(BaseModel):
    """返回前端的任务状态，不暴露宿主路径或 Worker 内部信息。"""

    id: str
    project_id: str
    user_id: str = Field(exclude=True)
    kind: ExecutionKind
    status: ExecutionStatus
    image: str
    argv: list[str]
    scan_profile: str | None = None
    timeout_seconds: int
    cpu_limit: float
    memory_mb: int
    pids_limit: int
    exit_code: int | None = None
    error: str | None = None
    output_truncated: bool = False
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExecutionEventView(BaseModel):
    """持久化审计事件或有界日志块。"""

    sequence: int
    type: str
    payload: dict


class ExecutionError(Exception):
    """可安全映射到 HTTP 的执行域错误。"""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.public_message = message
        self.status_code = status_code
