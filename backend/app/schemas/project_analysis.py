"""项目分析接口的响应模型。"""

from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.dependency_graph import DependencyGraphDTO
from backend.app.schemas.manifest import ProjectManifest


class ProjectAnalysisDataDTO(BaseModel):
    """一次完整项目分析返回给前端的数据。"""

    project_id: str
    sanitize_report: dict[str, int] = Field(default_factory=dict)
    file_tree: list[dict[str, Any]] = Field(default_factory=list)
    dependency_graph: DependencyGraphDTO
    project_manifest: ProjectManifest
    project_overview: dict[str, Any] = Field(default_factory=dict)


class ProjectAnalysisResponse(BaseModel):
    """保持现有 code/message/data 外层协议的强类型响应。"""

    code: int = 200
    message: str = "Project processed successfully."
    data: ProjectAnalysisDataDTO