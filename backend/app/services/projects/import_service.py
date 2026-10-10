"""项目来源获取、分析、发布和持久化的应用用例。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.project_analysis.graph_exchange import GraphExchangeNormalizer
from backend.app.services.project_analysis.pipeline import (
    ProjectAnalysisBundle,
    ProjectAnalysisPipeline,
    ProjectAnalysisPipelineError,
)
from backend.app.services.project_analysis.progress import report_progress
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.exceptions import WorkspaceError
from backend.app.services.security_analysis import SecurityAnalysisService

from .artifacts import ProjectArtifactRepository
from .contracts import AnalyzeProjectCommand, ProjectAnalysisResult
from .errors import (
    ArtifactPersistenceError,
    DependencyAnalysisError,
    ProjectAnalysisError,
    ProjectPersistenceError,
)
from .progress import ImportProgressTracker
from .repository import ProjectRepository
from .transaction import ProjectImportTransaction

logger = logging.getLogger(__name__)


class ProjectImportService:
    """在补偿事务中编排源码获取、纯分析、发布和项目持久化。"""

    def __init__(
        self,
        *,
        project_repository: ProjectRepository | None = None,
        artifact_repository: ProjectArtifactRepository | None = None,
        workspace_service: ProjectWorkspaceService | None = None,
        analysis_pipeline: ProjectAnalysisPipeline | None = None,
        graph_normalizer: GraphExchangeNormalizer | None = None,
        analyzer_factory: Callable[..., Any] = UnifiedCodeAnalyzer,
        security_analysis_service: SecurityAnalysisService | None = None,
    ) -> None:
        """注入项目、产物、工作区和纯分析流水线。"""
        self._projects = project_repository or ProjectRepository()
        self._artifacts = artifact_repository or ProjectArtifactRepository()
        self._workspace = workspace_service or ProjectWorkspaceService()
        self._analysis = analysis_pipeline or ProjectAnalysisPipeline(
            analyzer_factory=analyzer_factory,
            graph_normalizer=graph_normalizer,
            security_analysis_service=security_analysis_service,
        )

    async def import_project(
        self, command: AnalyzeProjectCommand, *, progress: ImportProgressTracker | None = None,
    ) -> ProjectAnalysisResult:
        """在线程池执行阻塞导入和静态分析。"""
        return await asyncio.to_thread(self._tracked_import_sync, command, progress)

    def _tracked_import_sync(
        self, command: AnalyzeProjectCommand, progress: ImportProgressTracker | None,
    ) -> ProjectAnalysisResult:
        """在线程内记录事务终态；浏览器断连不把仍在运行的任务误标为取消。"""
        try:
            result = self._import_sync(command, progress=progress)
        except ProjectAnalysisError as exc:
            if progress is not None:
                progress.fail(exc.public_message)
            raise
        if progress is not None:
            progress.complete(result.project_id)
        return result

    def _import_sync(
        self, command: AnalyzeProjectCommand, *, progress: ImportProgressTracker | None = None,
    ) -> ProjectAnalysisResult:
        """同步执行项目导入、分析、发布和持久化事务。"""
        transaction = ProjectImportTransaction(self._workspace, self._artifacts)
        try:
            with transaction:
                report_progress(progress, "preparing")
                operation = transaction.begin(command.user_id)
                prepared = self._workspace.prepare(operation, command.source)
                transaction.transition("analyzing")
                bundle = self._analysis.analyze(
                    str(operation.source_root), command.max_workers,
                    **({"progress": progress} if progress is not None else {}),
                )
                result = ProjectAnalysisResult(
                    project_id=operation.project_id,
                    sanitize_report=prepared.sanitize_report.to_dict(),
                    file_tree=bundle.file_tree,
                    dependency_graph=bundle.exchange_graph,
                    project_manifest=bundle.manifest,
                    deterministic_overview=bundle.overview,
                )
                report_progress(progress, "publishing")
                transaction.track_published_workspace()
                final_path = self._workspace.publish(prepared)
                transaction.track_artifact()
                transaction.transition("persisting")
                report_progress(progress, "persisting")
                self._save_artifact(operation.project_id, bundle)
                try:
                    self._projects.create(
                        project_id=operation.project_id,
                        user_id=command.user_id,
                        source=prepared.source_tag,
                        local_path=str(final_path),
                        file_tree=bundle.file_tree,
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
        except ProjectAnalysisPipelineError as exc:
            raise DependencyAnalysisError() from exc
        except ProjectAnalysisError:
            raise
        except Exception as exc:
            logger.exception("Unexpected project import failure")
            raise DependencyAnalysisError() from exc

    def _save_artifact(self, project_id: str, bundle: ProjectAnalysisBundle) -> None:
        """保存完整原始分析产物，并把持久化失败映射为稳定异常。"""
        evidence = bundle.security_evidence
        try:
            self._artifacts.save(project_id, {
                "manifest": bundle.manifest.model_dump(),
                "repo_map": bundle.repo_map,
                "overview": bundle.overview,
                "file_symbols": bundle.file_symbols,
                "dependency_graph": bundle.raw_graph,
                "semantic_index": bundle.semantic_index,
                "analysis_statistics": bundle.statistics,
                "analysis_diagnostics": bundle.diagnostics,
                "analysis_metadata": {
                    "schema_version": "2.0",
                    "analyzer": "ProjectAnalysisPipeline",
                    "graph_kind": "multidigraph" if bundle.raw_graph.get("multigraph") else "digraph",
                    "security_evidence_kind": "hybrid_static",
                    "semantic_index_schema_version": str(
                        bundle.semantic_index.get("schema_version") or ""
                    ),
                    "security_schema_version": evidence.schema_version,
                    "security_ir_version": evidence.ir_version,
                    "security_rule_packs": evidence.rule_packs,
                    "dataflow_verified": evidence.dataflow_verified,
                    "security_evidence_completed": evidence.completed,
                },
                "security_evidence": evidence.model_dump(),
            })
        except Exception as exc:
            raise ArtifactPersistenceError() from exc


__all__ = ["ProjectImportService"]
