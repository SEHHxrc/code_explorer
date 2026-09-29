"""项目导入、分析、发布和持久化的事务化应用服务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.app.services.analyzer import build_file_tree_with_symbols
from backend.app.services.code_intelligence.manifest_builder import (
    ProjectManifestBuilder,
)
from backend.app.services.code_intelligence.repo_map_builder import build_repo_map
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.project_workspace import ProjectWorkspaceService
from backend.app.services.project_workspace.exceptions import WorkspaceError
from backend.app.services.reports.overview_report import render_deterministic_overview
from backend.app.services.security_analysis import (
    SecurityAnalysisService,
    SecurityEvidencePack,
)

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

    def __init__(
        self,
        *,
        project_repository: ProjectRepository | None = None,
        artifact_repository: AnalysisArtifactRepository | None = None,
        graph_normalizer: GraphExchangeNormalizer | None = None,
        workspace_service: ProjectWorkspaceService | None = None,
        analyzer_factory: Callable[..., Any] = UnifiedCodeAnalyzer,
        security_analysis_service: SecurityAnalysisService | None = None,
    ) -> None:
        """注入项目仓储、产物仓储、图规范化器、工作区和分析器工厂。"""
        self._projects = project_repository or ProjectRepository()
        self._artifacts = artifact_repository or AnalysisArtifactRepository()
        self._graph_normalizer = graph_normalizer or GraphExchangeNormalizer()
        self._workspace = workspace_service or ProjectWorkspaceService()
        self._analyzer_factory = analyzer_factory
        self._security_analysis = security_analysis_service or SecurityAnalysisService()

    async def analyze(self, command: AnalyzeProjectCommand) -> ProjectAnalysisResult:
        """在线程池执行阻塞导入和静态分析，避免阻塞 FastAPI 事件循环。"""
        return await asyncio.to_thread(self._analyze_sync, command)

    def _analyze_sync(self, command: AnalyzeProjectCommand) -> ProjectAnalysisResult:
        """在线程中执行同步项目导入与分析事务。"""
        transaction = ProjectAnalysisTransaction(self._workspace, self._artifacts)
        try:
            with transaction:
                operation = transaction.begin(command.user_id)
                prepared = self._workspace.prepare(operation, command.source)
                transaction.transition("analyzing")
                analysis = self._run_analysis(str(operation.source_root), command.max_workers)
                raw_graph = analysis["dependency_graph"]
                file_symbols = analysis["file_symbols"]
                semantic_index = analysis.get("semantic_index") or {}
                security_evidence, semantic_index = self._run_security_analysis(
                    project_root=str(operation.source_root),
                    dependency_graph=raw_graph,
                    diagnostics=analysis.get("diagnostics") or {},
                    semantic_index=semantic_index,
                )
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
                        "semantic_index": semantic_index,
                        "analysis_statistics": analysis.get("stats") or {},
                        "analysis_diagnostics": analysis.get("diagnostics") or {},
                        "analysis_metadata": {
                            "schema_version": "2.0",
                            "analyzer": getattr(
                                self._analyzer_factory,
                                "__name__",
                                type(self._analyzer_factory).__name__,
                            ),
                            "graph_kind": "multidigraph" if raw_graph.get("multigraph") else "digraph",
                            "security_evidence_kind": "hybrid_static",
                            "semantic_index_schema_version": str(
                                semantic_index.get("schema_version") or ""
                            ),
                            "security_schema_version": security_evidence.schema_version,
                            "security_ir_version": security_evidence.ir_version,
                            "security_rule_packs": security_evidence.rule_packs,
                            "dataflow_verified": security_evidence.dataflow_verified,
                            "security_evidence_completed": security_evidence.completed,
                        },
                        "security_evidence": security_evidence.model_dump(),
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

    def _run_analysis(self, target_dir: str, max_workers: int) -> dict[str, Any]:
        """运行依赖分析并返回图、符号、统计和诊断组成的完整契约。"""
        try:
            analyzer = self._analyzer_factory(target_dir, max_workers=max(1, min(max_workers, 16)))
            result = analyzer.run_full_analysis()
            raw_graph = result.get("dependency_graph")
            file_symbols = result.get("file_symbols")
            if not isinstance(raw_graph, dict) or not isinstance(file_symbols, dict):
                raise TypeError("Analyzer returned an invalid result contract")
            stats = result.get("stats") or {}
            diagnostics = result.get("diagnostics") or {}
            semantic_index = result.get("semantic_index") or {}
            if (
                not isinstance(stats, dict)
                or not isinstance(diagnostics, dict)
                or not isinstance(semantic_index, dict)
            ):
                raise TypeError("Analyzer returned invalid diagnostics")
            return {
                "dependency_graph": raw_graph,
                "file_symbols": file_symbols,
                "semantic_index": semantic_index,
                "stats": stats,
                "diagnostics": diagnostics,
            }
        except Exception as exc:
            logger.exception("Dependency analysis failed for imported project")
            raise DependencyAnalysisError() from exc

    def _run_security_analysis(
        self,
        *,
        project_root: str,
        dependency_graph: dict[str, Any],
        diagnostics: dict[str, Any],
        semantic_index: dict[str, Any] | None = None,
    ) -> tuple[SecurityEvidencePack, dict[str, Any]]:
        """生成静态安全结构证据；失败时显式降级而不伪造空分析成功。"""
        try:
            return self._security_analysis.analyze_with_semantic_index(
                project_root=project_root,
                dependency_graph=dependency_graph,
                analysis_diagnostics=diagnostics,
                semantic_index=semantic_index,
            )
        except Exception:
            logger.exception("Security structural evidence generation failed")
            return (
                SecurityEvidencePack(
                    completed=False,
                    limitations=[
                        "静态安全结构证据生成失败；依赖分析产物仍可使用，但本项目没有可用的安全证据包。"
                    ],
                ),
                semantic_index or {},
            )
