"""对已存在源码目录执行确定性项目分析的纯应用流水线。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.app.schemas.dependency_graph import DependencyGraphDTO
from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.analyzer import build_file_tree_with_symbols
from backend.app.services.code_intelligence.manifest_builder import ProjectManifestBuilder
from backend.app.services.code_intelligence.repo_map_builder import build_repo_map
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.reports.overview_report import render_deterministic_overview
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidencePack

from .graph_exchange import GraphExchangeNormalizer
from .progress import AnalysisProgressReporter, report_progress

logger = logging.getLogger(__name__)


class ProjectAnalysisPipelineError(Exception):
    """纯项目分析流水线无法产生可信结果。"""


@dataclass(frozen=True)
class ProjectAnalysisBundle:
    """项目目录分析后产生的原始事实、派生数据和交换投影。"""

    raw_graph: dict[str, Any]
    file_symbols: dict[str, list[dict[str, Any]]]
    semantic_index: dict[str, Any]
    statistics: dict[str, Any]
    diagnostics: dict[str, Any]
    security_evidence: SecurityEvidencePack
    file_tree: list[dict[str, Any]]
    manifest: ProjectManifest
    repo_map: str
    overview: str
    exchange_graph: DependencyGraphDTO


class ProjectAnalysisPipeline:
    """只分析给定项目目录，不获取源码、不发布工作区也不持久化项目。"""

    def __init__(
        self,
        *,
        analyzer_factory: Callable[..., Any] = UnifiedCodeAnalyzer,
        graph_normalizer: GraphExchangeNormalizer | None = None,
        security_analysis_service: SecurityAnalysisService | None = None,
    ) -> None:
        """注入依赖分析器、图交换投影和安全分析服务。"""
        self._analyzer_factory = analyzer_factory
        self._graph_normalizer = graph_normalizer or GraphExchangeNormalizer()
        self._security_analysis = security_analysis_service or SecurityAnalysisService()

    def analyze(
        self, project_root: str, max_workers: int = 4, *,
        progress: AnalysisProgressReporter | None = None,
    ) -> ProjectAnalysisBundle:
        """分析已存在的源码目录并返回不含持久化副作用的完整结果。"""
        analysis = self._run_dependency_analysis(project_root, max_workers, progress=progress)
        raw_graph = analysis["dependency_graph"]
        file_symbols = analysis["file_symbols"]
        semantic_index = analysis["semantic_index"]
        security_evidence, semantic_index = self._run_security_analysis(
            project_root=project_root,
            dependency_graph=raw_graph,
            diagnostics=analysis["diagnostics"],
            semantic_index=semantic_index,
            progress=progress,
        )
        report_progress(progress, "projection")
        file_tree = build_file_tree_with_symbols(project_root, file_symbols)
        manifest = ProjectManifestBuilder(project_root).build(raw_graph)
        repo_map = build_repo_map(manifest, file_symbols)
        overview = render_deterministic_overview(manifest)
        return ProjectAnalysisBundle(
            raw_graph=raw_graph,
            file_symbols=file_symbols,
            semantic_index=semantic_index,
            statistics=analysis["stats"],
            diagnostics=analysis["diagnostics"],
            security_evidence=security_evidence,
            file_tree=file_tree,
            manifest=manifest,
            repo_map=repo_map,
            overview=overview,
            exchange_graph=self._graph_normalizer.normalize(raw_graph),
        )

    def _run_dependency_analysis(
        self,
        project_root: str,
        max_workers: int,
        *, progress: AnalysisProgressReporter | None = None,
    ) -> dict[str, Any]:
        """运行依赖分析并校验图、符号、统计和诊断契约。"""
        try:
            analyzer = self._analyzer_factory(
                project_root,
                max_workers=max(1, min(max_workers, 16)),
            )
            report_progress(progress, "dependency", getattr(analyzer, "get_progress", None))
            result = analyzer.run_full_analysis()
            raw_graph = result.get("dependency_graph")
            file_symbols = result.get("file_symbols")
            stats = result.get("stats") or {}
            diagnostics = result.get("diagnostics") or {}
            semantic_index = result.get("semantic_index") or {}
            if not isinstance(raw_graph, dict) or not isinstance(file_symbols, dict):
                raise TypeError("Analyzer returned an invalid result contract")
            if not all(isinstance(value, dict) for value in (stats, diagnostics, semantic_index)):
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
            raise ProjectAnalysisPipelineError("Project analysis pipeline failed") from exc

    def _run_security_analysis(
        self,
        *,
        project_root: str,
        dependency_graph: dict[str, Any],
        diagnostics: dict[str, Any],
        semantic_index: dict[str, Any],
        progress: AnalysisProgressReporter | None = None,
    ) -> tuple[SecurityEvidencePack, dict[str, Any]]:
        """生成安全证据；失败时显式返回未完成状态而不伪造成功。"""
        try:
            report_progress(progress, "security_scan")
            return self._security_analysis.analyze_with_semantic_index(
                project_root=project_root,
                dependency_graph=dependency_graph,
                analysis_diagnostics=diagnostics,
                semantic_index=semantic_index,
                **({"progress": progress} if progress is not None else {}),
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
                semantic_index,
            )


__all__ = [
    "ProjectAnalysisBundle",
    "ProjectAnalysisPipeline",
    "ProjectAnalysisPipelineError",
]
