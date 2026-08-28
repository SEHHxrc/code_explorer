"""Web 控制面使用的执行任务应用服务。"""

from __future__ import annotations

import shutil
import uuid

from backend.app.services.project_analysis.repository import ProjectRepository

from .contracts import ExecutionError, ExecutionTaskRequest
from .policy import ExecutionPolicy, ExecutionSettings
from .repository import ExecutionRepository


class ExecutionService:
    """校验所有权与策略后入队；不在 Web 进程中启动 Docker。"""

    def __init__(self, *, repository=None, projects=None, policy=None):
        self.repository = repository or ExecutionRepository()
        self.projects = projects or ProjectRepository()
        self.policy = policy or ExecutionPolicy()

    def configuration(self) -> dict:
        """输出不含宿主路径和密钥的执行能力状态。"""
        settings = self.policy.settings
        return {
            "configured": settings.configured,
            "docker_cli_available_on_api_host": shutil.which(settings.docker_binary) is not None,
            "allowed_images": sorted(settings.allowed_images),
            "scan_profiles": sorted(settings.scan_images),
            "limits": {
                "timeout_seconds": settings.max_timeout_seconds,
                "cpu": settings.max_cpu,
                "memory_mb": settings.max_memory_mb,
                "pids": settings.max_pids,
                "output_bytes": settings.max_output_bytes,
                "active_tasks_per_user": settings.max_active_tasks_per_user,
            },
            "network": "none",
            "workspace": "read_only",
        }

    def submit(self, project_id: str, user_id: str, request: ExecutionTaskRequest) -> dict:
        """创建经过策略验证的任务，返回任务视图和 SSE 地址。"""
        if self.projects.get_owned(project_id, user_id) is None:
            raise ExecutionError("Project not found or unauthorized.", 404)
        if self.repository.count_active_for_user(user_id) >= self.policy.settings.max_active_tasks_per_user:
            raise ExecutionError("Too many active execution tasks for this user.", 429)
        plan = self.policy.resolve(request)
        task_id = uuid.uuid4().hex
        view = self.repository.create(
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            plan=plan,
        )
        return {**view.model_dump(), "events_url": f"/api/executions/tasks/{task_id}/events"}

    def get(self, task_id: str, user_id: str):
        """读取用户拥有的任务，否则输出 404 领域错误。"""
        view = self.repository.get(task_id, user_id)
        if view is None:
            raise ExecutionError("Execution task not found.", 404)
        return view

    def list_for_project(self, project_id: str, user_id: str, limit: int = 20):
        """列出用户项目的近期任务。"""
        if self.projects.get_owned(project_id, user_id) is None:
            raise ExecutionError("Project not found or unauthorized.", 404)
        return self.repository.list_for_project(project_id, user_id, limit)

    def cancel(self, task_id: str, user_id: str):
        """请求取消任务；终态任务原样返回。"""
        if self.repository.get(task_id, user_id) is None:
            raise ExecutionError("Execution task not found.", 404)
        return self.repository.request_cancel(task_id, user_id)
