"""项目分析用例的输入、输出契约。"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.schemas.dependency_graph import DependencyGraphDTO
from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.project_workspace import WorkspaceSource

ProjectSource = WorkspaceSource


@dataclass(frozen=True)
class AnalyzeProjectCommand:
    """执行一次项目分析所需的用户、来源和并发参数。"""

    user_id: str
    source: ProjectSource
    max_workers: int = 4


@dataclass(frozen=True)
class ProjectAnalysisResult:
    """应用服务返回给 API 层的完整分析结果。"""

    project_id: str
    sanitize_report: dict[str, int]
    file_tree: list[dict]
    dependency_graph: DependencyGraphDTO
    project_manifest: ProjectManifest
    deterministic_overview: str
