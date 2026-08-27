"""项目导入、分析、发布和持久化的事务化应用服务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.app.services.analyzer import build_file_tree_with_symbols
from backend.app.services.code_intelligence.manifest_builder import ProjectManifestBuilder
from backend.app.services.code_intelligence.repo_map_builder import build_repo_map
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.exceptions import WorkspaceError
from backend.app.services.reports.overview_report import render_deterministic_overview

from .artifact_repository import AnalysisArtifactRepository
from .contracts import AnalyzeProjectCommand, ProjectAnalysisResult
from .exceptions import (
    ArtifactPersistenceError,
    DependencyAnalysisError,
    ProjectAnalysisError,
    ProjectPersistenceError,
)
from .graph_exchange import GraphExchangeNormalizer
from .repository import ProjectRepository
from .transaction import ProjectAnalysisTransaction

logger = logging.getLogger(__name__)


class ProjectAnalysisService:
    """在可恢复补偿事务中编排工作区、分析产物和项目数据库记录。"""

    def __init__(self, *, project_repository=None, artifact_repository=None, graph_normalizer=None, workspace_service=None, analyzer_factory: Callable[..., Any] = UnifiedCodeAnalyzer):
        self._projects = project_repository or ProjectRepository()
        self._artifacts = artifact_repository or AnalysisArtifactRepository()
        self._graph_normalizer = graph_normalizer or GraphExchangeNormalizer()
        self._workspace = workspace_service or ProjectWorkspaceService()
        self._analyzer_factory = analyzer_factory

    async def analyze(self, command: AnalyzeProjectCommand) -> ProjectAnalysisResult:
        """在线程池执行阻塞导入和静态分析，避免阻塞 FastAPI 事件循环。"""
        return await asyncio.to_thread(self._analyze_sync, command)

    def _analyze_sync(self, command: AnalyzeProjectCommand) -> ProjectAnalysisResult:
        transaction = ProjectAnalysisTransaction(self._workspace, self._artifacts)
        try:
            with transaction:
                operation = transaction.begin(command.user_id)
                prepared = self._workspace.prepare(operation, command.source)
                transaction.transition("analyzing")
                raw_graph, file_symbols = self._run_analysis(str(operation.source_root), command.max_workers)
                file_tree = build_file_tree_with_symbols(str(operation.source_root), file_symbols)
                manifest = ProjectManifestBuilder(str(operation.source_root)).build(raw_graph)
                repo_map = build_repo_map(manifest, file_symbols)
                overview = render_deterministic_overview(manifest)
                exchange_graph = self._graph_normalizer.normalize(raw_graph)
                result = ProjectAnalysisResult(
                    project_id=operation.project_id,
                    sanitize_report=prepared.sanitize_report.to_dict(),
                    file_tree=file_tree,
                    dependency_graph=exchange_graph,
                    project_manifest=manifest,
                    deterministic_overview=overview,
                )

                transaction.track_project()
                final_path = self._workspace.publish(prepared)
                transaction.track_artifact()
                transaction.transition("persisting")
                try:
                    self._artifacts.save(operation.project_id, {
                        "manifest": manifest.model_dump(),
                        "repo_map": repo_map,
                        "overview": overview,
                        "file_symbols": file_symbols,
                        "dependency_graph": raw_graph,
                    })
                except Exception as exc:
                    raise ArtifactPersistenceError() from exc
                try:
                    self._projects.create(
                        project_id=operation.project_id,
                        user_id=command.user_id,
                        source=prepared.source_tag,
                        local_path=str(final_path),
                        file_tree=file_tree,
                    )
                except Exception as exc:
                    raise ProjectPersistenceError() from exc
                transaction.commit()
                return result
        except WorkspaceError as exc:
            raise ProjectAnalysisError(
                exc.public_message,
                stage=exc.stage,
                status_code=exc.status_code,
            ) from exc
        except ProjectAnalysisError:
            raise
        except Exception as exc:
            logger.exception("Unexpected project analysis failure")
            raise DependencyAnalysisError() from exc

    def _run_analysis(self, target_dir: str, max_workers: int) -> tuple[dict, dict]:
        try:
            analyzer = self._analyzer_factory(target_dir, max_workers=max(1, min(max_workers, 16)))
            result = analyzer.run_full_analysis()
            raw_graph = result.get("dependency_graph")
            file_symbols = result.get("file_symbols")
            if not isinstance(raw_graph, dict) or not isinstance(file_symbols, dict):
                raise TypeError("Analyzer returned an invalid result contract")
            return raw_graph, file_symbols
        except Exception as exc:
            logger.exception("Dependency analysis failed for imported project")
            raise DependencyAnalysisError() from exc