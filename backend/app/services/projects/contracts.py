"""项目功能域的稳定命令、记录和结果契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.app.schemas.dependency_graph import DependencyGraphDTO
from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.project_workspace import WorkspaceSource

ProjectSource = WorkspaceSource


@dataclass(frozen=True)
class ProjectRecord:
    """脱离数据库会话的项目元数据快照。"""

    project_id: str
    user_id: str
    source: str
    local_path: str
    file_tree: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True)
class ProjectUpdate:
    """允许普通更新修改的项目字段白名单。"""

    file_tree: list[dict[str, Any]]


@dataclass(frozen=True)
class AnalyzeProjectCommand:
    """执行一次项目导入和分析所需的用户、来源与并发参数。"""

    user_id: str
    source: ProjectSource
    max_workers: int = 4


@dataclass(frozen=True)
class ProjectAnalysisResult:
    """项目导入用例返回给协议层的完整分析结果。"""

    project_id: str
    sanitize_report: dict[str, int]
    file_tree: list[dict[str, Any]]
    dependency_graph: DependencyGraphDTO
    project_manifest: ProjectManifest
    deterministic_overview: str


@dataclass(frozen=True)
class ProjectDeletionResult:
    """项目删除完成后的资源结果。"""

    project_id: str
    deleted: bool = True
    warnings: list[str] = field(default_factory=list)
