"""项目功能域的公开入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .import_service import ProjectImportService

from .artifacts import ProjectArtifactRepository
from .contracts import (
    AnalyzeProjectCommand,
    ProjectAnalysisResult,
    ProjectDeletionResult,
    ProjectRecord,
    ProjectSource,
    ProjectUpdate,
)
from .deletion_service import ProjectDeletionService
from .errors import (
    ArtifactPersistenceError,
    DependencyAnalysisError,
    ProjectAnalysisError,
    ProjectDeletionError,
    ProjectError,
    ProjectPersistenceError,
    ProjectQueryError,
)
from .query_service import ProjectQueryService
from .repository import ProjectDeletionRepository, ProjectRepository

__all__ = [
    "AnalyzeProjectCommand",
    "ArtifactPersistenceError",
    "DependencyAnalysisError",
    "ProjectAnalysisError",
    "ProjectAnalysisResult",
    "ProjectArtifactRepository",
    "ProjectDeletionError",
    "ProjectDeletionRepository",
    "ProjectDeletionResult",
    "ProjectDeletionService",
    "ProjectError",
    "ProjectImportService",
    "ProjectPersistenceError",
    "ProjectQueryError",
    "ProjectQueryService",
    "ProjectRecord",
    "ProjectRepository",
    "ProjectSource",
    "ProjectUpdate",
]


def __getattr__(name: str) -> Any:
    """延迟加载依赖静态分析器的项目导入服务。"""
    if name != "ProjectImportService":
        raise AttributeError(name)
    from .import_service import ProjectImportService

    return ProjectImportService
