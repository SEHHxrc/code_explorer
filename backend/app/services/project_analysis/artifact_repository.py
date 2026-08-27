"""分析产物存储的可替换仓储适配器。"""

from typing import Any

from backend.app.services.artifact_store import (
    remove_analysis_artifact,
    save_analysis_artifact,
)


class AnalysisArtifactRepository:
    """封装当前 JSON 产物实现，供应用服务注入与失败补偿。"""

    def save(self, project_id: str, payload: dict[str, Any]) -> None:
        save_analysis_artifact(project_id, payload)

    def remove(self, project_id: str) -> None:
        remove_analysis_artifact(project_id)