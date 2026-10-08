"""用户项目库存、存储占用统计和前端快照恢复服务。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.project_analysis.graph_exchange import GraphExchangeNormalizer
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths

from .artifacts import ProjectArtifactRepository
from .contracts import ProjectRecord
from .errors import ProjectQueryError
from .repository import ProjectDeletionRepository, ProjectRepository

MAX_SIZE_SCAN_ENTRIES = 200_000


class ProjectQueryService:
    """列出用户项目及资源状态，并重建前端分析快照。"""

    def __init__(
        self,
        *,
        projects: ProjectRepository | None = None,
        activity: ProjectDeletionRepository | None = None,
        paths: ProjectWorkspacePaths | None = None,
        artifacts: ProjectArtifactRepository | None = None,
        graph_normalizer: GraphExchangeNormalizer | None = None,
        artifact_loader: Callable[[str], dict[str, Any] | None] | None = None,
        artifact_sizer: Callable[[str], int | None] | None = None,
    ) -> None:
        """注入项目、活动任务、路径、产物和图交换边界。"""
        self.projects = projects or ProjectRepository()
        self.activity = activity or ProjectDeletionRepository()
        self.paths = paths or ProjectWorkspacePaths()
        self.artifacts = artifacts or ProjectArtifactRepository()
        self.artifact_loader = artifact_loader or self.artifacts.load
        self.artifact_sizer = artifact_sizer or self.artifacts.size
        self.graph_normalizer = graph_normalizer or GraphExchangeNormalizer()

    async def list(self, user_id: str) -> dict[str, Any]:
        """异步返回当前用户项目库存以及总占用字节数。"""
        return await asyncio.to_thread(self._list_sync, user_id)

    async def snapshot(self, project_id: str, user_id: str) -> dict[str, Any]:
        """异步恢复指定项目的前端分析快照。"""
        return await asyncio.to_thread(self._snapshot_sync, project_id, user_id)

    def _list_sync(self, user_id: str) -> dict[str, Any]:
        """同步扫描受控项目目录和分析产物元数据。"""
        items = [self._describe(record) for record in self.projects.list_owned(user_id)]
        return {
            "projects": items,
            "project_count": len(items),
            "total_bytes": sum(item["total_bytes"] for item in items),
        }

    def _describe(self, record: ProjectRecord) -> dict[str, Any]:
        """把项目记录转换为不暴露宿主绝对路径的库存条目。"""
        workspace = self.paths.project_root(record.user_id, record.project_id)
        workspace_bytes, workspace_exists, size_complete = self._directory_size(workspace)
        artifact_bytes = self.artifact_sizer(record.project_id)
        artifact: dict[str, Any] | None = None
        artifact_readable = False
        if artifact_bytes is not None:
            try:
                loaded = self.artifact_loader(record.project_id)
                artifact = loaded if isinstance(loaded, dict) else None
                artifact_readable = artifact is not None
            except (OSError, UnicodeError, ValueError):
                artifact = None
        manifest = artifact.get("manifest") if artifact is not None else {}
        name = manifest.get("project_name") if isinstance(manifest, dict) else None
        return {
            "project_id": record.project_id,
            "name": str(name or record.project_id),
            "source": record.source,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "workspace_exists": workspace_exists,
            "artifact_exists": artifact_bytes is not None,
            "artifact_readable": artifact_readable,
            "workspace_bytes": workspace_bytes,
            "artifact_bytes": artifact_bytes or 0,
            "total_bytes": workspace_bytes + (artifact_bytes or 0),
            "size_complete": size_complete,
            "active_tasks": self._has_active_tasks(record),
        }

    def _has_active_tasks(self, record: ProjectRecord) -> bool:
        """读取活动任务状态，并兼容迁移期旧仓储测试替身。"""
        legacy = getattr(self.projects, "has_active_runs", None)
        if callable(legacy):
            return bool(legacy(record.project_id, record.user_id))
        return self.activity.has_active_tasks(record.project_id, record.user_id)

    def _snapshot_sync(self, project_id: str, user_id: str) -> dict[str, Any]:
        """校验所有权和资源完整性后生成前端交换数据。"""
        record = self.projects.get_owned(project_id, user_id)
        if record is None:
            raise ProjectQueryError(
                "Project not found or unauthorized.",
                status_code=404,
                stage="query",
            )
        workspace = self.paths.project_root(user_id, project_id)
        if not workspace.is_dir() or workspace.is_symlink():
            raise ProjectQueryError(
                "Project workspace is missing; delete this stale record.",
                status_code=409,
                stage="query",
            )
        artifact = self.artifact_loader(project_id)
        if not artifact:
            raise ProjectQueryError(
                "Project analysis artifact is missing; delete this stale record.",
                status_code=409,
                stage="query",
            )
        try:
            manifest = ProjectManifest.model_validate(artifact.get("manifest") or {})
            graph = self.graph_normalizer.normalize(artifact.get("dependency_graph") or {})
        except Exception as exc:
            raise ProjectQueryError(
                "Project analysis artifact is invalid; re-import the project.",
                status_code=409,
                stage="query",
            ) from exc
        return {
            "project_id": project_id,
            "sanitize_report": {},
            "file_tree": record.file_tree,
            "dependency_graph": graph.model_dump(),
            "project_manifest": manifest.model_dump(),
            "project_overview": {
                "content": str(artifact.get("overview") or ""),
                "source": "static",
                "provider": None,
                "model": None,
            },
        }

    @staticmethod
    def _directory_size(root: Path) -> tuple[int, bool, bool]:
        """不跟随符号链接统计目录大小，并限制扫描条目数。"""
        if not root.is_dir() or root.is_symlink():
            return 0, False, True
        total = 0
        scanned = 0
        complete = True
        stack = [root]
        while stack and scanned < MAX_SIZE_SCAN_ENTRIES:
            directory = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > MAX_SIZE_SCAN_ENTRIES:
                            complete = False
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            complete = False
            except OSError:
                complete = False
        if stack:
            complete = False
        return total, True, complete

__all__ = ["ProjectQueryService"]
