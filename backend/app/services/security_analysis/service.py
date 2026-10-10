"""静态安全结构证据的应用服务。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.project_analysis.progress import AnalysisProgressReporter, report_progress
from backend.app.services.semantic_index import (
    ProgramGraphSemanticProvider,
    SemanticIndexArtifact,
    SemanticIndexEnricher,
)

from .contracts import (
    CodeSnippet,
    Confidence,
    SecurityCandidate,
    SecurityCoverage,
    SecurityEntrypoint,
    SecurityEvidencePack,
    StaticSecurityFact,
)
from .flow_analysis import DataFlowAnalyzer, ProgramGraphSecurityFlowAnalyzer
from .path_finder import StructuralCallPathFinder, StructuralPath
from .scanner import SecurityScanner
from .slicer import SourceSlicer

DEFAULT_LIMITATIONS = [
    "只有标记为 intra_procedural_dataflow 或 interprocedural_dataflow 的候选包含公共 ProgramGraph 到达定义路径；其余候选只表示结构可达性。",
    "当前 CFG 尚未覆盖全部语言特殊控制语义，也未证明具体分支在运行时可行。",
    "动态派发、反射、运行时导入和未解析引用可能造成漏报或不完整路径。",
    "当前规则包覆盖 Python/FastAPI、Java/Spring/Servlet、Go net/http、C/C++ 标准库/POSIX/SQLite，以及 JS/TS 的浏览器、Node.js、Express、Vue/React 核心语法；不是全量语言/框架扫描。",
]


class SecurityAnalysisService:
    """组合规则扫描、调用路径、源码切片和不确定性，生成证据包。"""

    def __init__(
        self,
        *,
        scanner: SecurityScanner | None = None,
        program_graph_service: ProgramGraphService | None = None,
        semantic_index_enricher: SemanticIndexEnricher | None = None,
        flow_analyzer: DataFlowAnalyzer | None = None,
        max_path_depth: int = 6,
        max_candidates: int = 500,
        max_sources: int = 300,
        max_sinks: int = 600,
        max_guards: int = 2_000,
        max_sanitizers: int = 1_000,
    ) -> None:
        """注入扫描器并设置证据包的路径和体量上限。"""
        self.scanner = scanner or SecurityScanner()
        self.program_graph_service = program_graph_service or ProgramGraphService()
        self.semantic_index_enricher = (
            semantic_index_enricher or ProgramGraphSemanticProvider()
        )
        self.flow_analyzer = flow_analyzer or ProgramGraphSecurityFlowAnalyzer(
            semantics=self.scanner.semantics,
        )
        self.max_path_depth = max(1, max_path_depth)
        self.max_candidates = max(1, max_candidates)
        self.max_sources = max(1, max_sources)
        self.max_sinks = max(1, max_sinks)
        self.max_guards = max(1, max_guards)
        self.max_sanitizers = max(1, max_sanitizers)

    def analyze(
        self,
        *,
        project_root: str | Path,
        dependency_graph: dict[str, Any],
        analysis_diagnostics: dict[str, Any] | None = None,
        semantic_index: dict[str, Any] | None = None,
    ) -> SecurityEvidencePack:
        """输入项目和无损调用图，输出不含完整图的安全结构证据包。"""
        evidence, _ = self.analyze_with_semantic_index(
            project_root=project_root,
            dependency_graph=dependency_graph,
            analysis_diagnostics=analysis_diagnostics,
            semantic_index=semantic_index,
        )
        return evidence

    def analyze_with_semantic_index(
        self,
        *,
        project_root: str | Path,
        dependency_graph: dict[str, Any],
        analysis_diagnostics: dict[str, Any] | None = None,
        semantic_index: dict[str, Any] | None = None,
        progress: AnalysisProgressReporter | None = None,
    ) -> tuple[SecurityEvidencePack, dict[str, Any]]:
        """生成安全证据，并返回经 ProgramGraph 补充后的语义索引。"""
        enriched_index = SemanticIndexArtifact.model_validate(semantic_index or {})
        report_progress(progress, "security_scan")
        scan = self.scanner.scan(project_root, dependency_graph=dependency_graph)
        try:
            report_progress(progress, "program_graph")
            program_graph = self.program_graph_service.analyze(
                project_root,
                dependency_graph=dependency_graph,
                languages=set(scan.languages_analyzed),
            )
            enriched_index = self.semantic_index_enricher.enrich(
                enriched_index,
                program_graph=program_graph,
            )
            report_progress(progress, "dataflow")
            scan.dataflows = self.flow_analyzer.analyze(
                program_graph,
                sources=scan.sources,
                sinks=scan.sinks,
                sanitizers=scan.sanitizers,
                dependency_graph=dependency_graph,
                semantic_index=enriched_index,
                value_boundaries=scan.value_boundaries,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            scan.failures.append({
                "reason": "program_graph_dataflow_error",
                "detail": type(exc).__name__,
            })
        report_progress(progress, "evidence")
        entrypoints = self._sorted_entrypoints(scan.entrypoints)
        sources = self._sorted(scan.sources)
        sinks = self._sorted(scan.sinks)
        guards = self._sorted(scan.guards)
        sanitizers = self._sorted(scan.sanitizers)
        limitations = list(DEFAULT_LIMITATIONS)
        if set(scan.languages_analyzed).intersection({"javascript", "typescript"}):
            limitations.append(
                "JS/TS 已有限连接原生表单/useState、Vue v-model/ref 与请求体 data→end；尚未建模完整回调调度、任意闭包、完整 hooks、Promise 时序和堆别名。此项是能力边界，不表示项目实际使用了全部这些特性。"
            )
        if set(scan.languages_analyzed).intersection({"c", "cpp"}):
            limitations.append(
                "C/C++ 库 API 采用头文件与词法名称过滤，仅支持独立调用的具名输出缓冲区 may 写入；尚未执行预处理、完整类型绑定、指针别名或通用副作用分析。"
            )
        if scan.failures:
            limitations.append(f"安全前端产生 {len(scan.failures)} 条失败或不确定性诊断。")
        if any(
            item.get("reason") == "program_graph_dataflow_error"
            for item in scan.failures
        ):
            limitations.append("公共 ProgramGraph 数据流构建失败；本次只保留结构安全证据。")
        if scan.unsupported_languages:
            limitations.append(
                "尚未注册安全前端的语言：" + ", ".join(scan.unsupported_languages) + "。"
            )
        for values, limit, label in (
            (sources, self.max_sources, "Source"),
            (sinks, self.max_sinks, "Sink"),
            (guards, self.max_guards, "Guard"),
            (sanitizers, self.max_sanitizers, "Sanitizer"),
        ):
            if len(values) > limit:
                limitations.append(f"{label} 事实超过证据包上限，仅保留前 {limit} 条。")

        retained_sources = sources[:self.max_sources]
        retained_sinks = sinks[:self.max_sinks]
        retained_guards = guards[:self.max_guards]
        retained_sanitizers = sanitizers[:self.max_sanitizers]
        path_finder = StructuralCallPathFinder(
            dependency_graph,
            max_depth=self.max_path_depth,
        )
        slicer = SourceSlicer(project_root)
        diagnostics = analysis_diagnostics or {}
        unresolved = diagnostics.get("unresolved_references") or []
        guards_by_symbol = self._by_symbol(retained_guards)
        sanitizers_by_symbol = self._by_symbol(retained_sanitizers)
        candidates: list[SecurityCandidate] = []
        call_edges = {}
        snippets = {}
        dataflows = {item.flow_id: item for item in scan.dataflows}
        dataflow_by_pair = {
            (item.source_fact_id, item.sink_fact_id): item
            for item in scan.dataflows
        }
        candidate_limit_reached = False
        sink_symbols = {sink.symbol for sink in retained_sinks}
        reachable_by_source: dict[str, dict[str, StructuralPath]] = {}

        for source in retained_sources:
            if source.symbol not in reachable_by_source:
                reachable_by_source[source.symbol] = path_finder.find_reachable(
                    source.symbol,
                    sink_symbols,
                )
            for sink in retained_sinks:
                path = reachable_by_source[source.symbol].get(sink.symbol)
                dataflow = dataflow_by_pair.get((source.fact_id, sink.fact_id))
                if path is None and dataflow is not None:
                    path = StructuralPath(
                        nodes=list(dict.fromkeys((source.symbol, sink.symbol))),
                        edges=path_finder.edges(dataflow.call_edge_ids),
                    )
                if path is None:
                    continue
                candidate, candidate_snippets = self._candidate(
                    source=source,
                    sink=sink,
                    path=path,
                    guards_by_symbol=guards_by_symbol,
                    sanitizers_by_symbol=sanitizers_by_symbol,
                    unresolved=unresolved,
                    slicer=slicer,
                    dataflow=dataflow,
                )
                candidates.append(candidate)
                for edge in path.edges:
                    call_edges.setdefault(edge.edge_id, edge)
                for snippet in candidate_snippets:
                    snippets.setdefault(snippet.snippet_id, snippet)
                if len(candidates) > self.max_candidates:
                    candidate_limit_reached = True
                    break
            if candidate_limit_reached:
                break

        if candidate_limit_reached:
            candidates = candidates[:self.max_candidates]
            limitations.append(
                f"安全候选达到 {self.max_candidates} 条上限；完整计数需要提高预算后重新分析。"
            )
        coverage = SecurityCoverage(
            files_considered=scan.files_considered,
            files_scanned=scan.files_scanned,
            parse_failures=sum(
                str(item.get("reason") or "").endswith("_parse_error")
                for item in scan.failures
            ),
            skipped_files=sum(
                item.get("reason") == "file_too_large" for item in scan.failures
            ),
            diagnostic_count=len(scan.failures),
            unsupported_language_count=len(scan.unsupported_languages),
            entrypoint_count=len(entrypoints),
            source_count=len(sources),
            sink_count=len(sinks),
            guard_count=len(guards),
            sanitizer_count=len(sanitizers),
            candidate_count=len(candidates),
            dataflow_count=len(dataflows),
            candidate_limit_reached=candidate_limit_reached,
        )
        retained_facts = (
            retained_sources + retained_sinks + retained_guards + retained_sanitizers
        )
        # 即使没有 Source→Sink 候选，也保存已观察输入/危险 API 的位置与脱敏片段。
        for fact in retained_sources + retained_sinks:
            snippet = slicer.slice_fact(fact, "source" if fact.fact_kind == "source" else "sink")
            if snippet is not None:
                snippets.setdefault(snippet.snippet_id, snippet)
                fact.metadata["snippet_id"] = snippet.snippet_id
        evidence = SecurityEvidencePack(
            rule_packs=scan.rule_packs,
            rule_coverage=scan.rule_coverage,
            languages_analyzed=scan.languages_analyzed,
            unsupported_languages=scan.unsupported_languages,
            entrypoints={item.entrypoint_id: item for item in entrypoints},
            facts={item.fact_id: item for item in retained_facts},
            call_edges=call_edges,
            snippets=snippets,
            dataflows=dataflows,
            candidates=candidates,
            coverage=coverage,
            scan_failures=scan.failures,
            limitations=limitations,
            dataflow_verified=bool(dataflows),
        )
        return evidence, enriched_index.model_dump()

    def _candidate(
        self,
        *,
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        path: StructuralPath,
        guards_by_symbol: dict[str, list[StaticSecurityFact]],
        sanitizers_by_symbol: dict[str, list[StaticSecurityFact]],
        unresolved: list[dict[str, Any]],
        slicer: SourceSlicer,
        dataflow: Any | None,
    ) -> tuple[SecurityCandidate, list[CodeSnippet]]:
        """把结构路径组装为只引用去重实体的候选及其片段。"""
        path_guards = self._facts_on_path(path.nodes, guards_by_symbol, limit=12)
        path_sanitizers = self._facts_on_path(path.nodes, sanitizers_by_symbol, limit=12)
        relevant_unresolved = [
            item for item in unresolved
            if isinstance(item, dict) and str(item.get("from_fqn") or "") in path.nodes
        ]
        unresolved_truncated = len(relevant_unresolved) > 20
        relevant_unresolved = relevant_unresolved[:20]
        snippets = self._snippets(source, sink, path_guards, path, slicer)
        edge_ids = ",".join(edge.edge_id for edge in path.edges)
        material = f"{source.fact_id}\x1f{sink.fact_id}\x1f{edge_ids}"
        candidate_id = "candidate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        truncated = (
            unresolved_truncated
            or any(edge.truncated for edge in path.edges)
            or any(snippet.truncated for snippet in snippets)
        )
        limitations = [] if dataflow is not None else [
            "该候选只证明 Source 与 Sink 所在函数之间存在结构调用路径。",
            "尚未验证 Source 值是否沿变量、字段或容器传播到 Sink 参数。",
        ]
        limitations.extend(dict.fromkeys(
            (source.metadata.get("limitations") or [])
            + (sink.metadata.get("limitations") or [])
        ))
        if dataflow is not None:
            limitations.extend(dataflow.unresolved)
            if dataflow.value_boundary_ids:
                limitations.append("本路径包含静态适配的 may 状态/闭包值边界，不是普通 CALLS 边，也不证明完整事件或重渲染生命周期。")
            if dataflow.scope == "interprocedural":
                limitations.append(
                    "跨过程数据流通过已解析调用或有静态依据的有界值边界连接；尚未覆盖完整堆对象、容器别名和运行时生命周期。"
                )
            else:
                limitations.append(
                    "数据流在单个函数分区内验证；尚未覆盖字段、容器和完整对象别名。"
                )
        if relevant_unresolved:
            limitations.append("候选路径相关函数中存在未解析引用，路径可能不完整。")
        candidate = SecurityCandidate(
            candidate_id=candidate_id,
            rule_id=sink.rule_id,
            cwe=sink.cwe,
            category=sink.category,
            severity=sink.severity,
            confidence=(dataflow.confidence if dataflow is not None else self._path_confidence(path)),
            path_kind=(
                (
                    "interprocedural_dataflow"
                    if dataflow.scope == "interprocedural"
                    else "intra_procedural_dataflow"
                ) if dataflow is not None
                else "structural_call_path"
            ),
            dataflow_verified=dataflow is not None,
            taint_status=dataflow.status if dataflow is not None else "not_analyzed",
            dataflow_id=dataflow.flow_id if dataflow is not None else None,
            source_fact_id=source.fact_id,
            sink_fact_id=sink.fact_id,
            call_edge_ids=[edge.edge_id for edge in path.edges],
            guard_fact_ids=[fact.fact_id for fact in path_guards],
            sanitizer_fact_ids=[fact.fact_id for fact in path_sanitizers],
            snippet_ids=[snippet.snippet_id for snippet in snippets],
            unresolved_boundaries=relevant_unresolved,
            preconditions=self._preconditions(
                source,
                sink,
                dataflow_verified=dataflow is not None,
            ),
            limitations=list(dict.fromkeys(limitations)),
            truncated=truncated,
        )
        return candidate, snippets

    @staticmethod
    def _snippets(
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        guards: list[StaticSecurityFact],
        path: StructuralPath,
        slicer: SourceSlicer,
    ) -> list[CodeSnippet]:
        """提取 Source、Sink、Guard 和少量中间调用点片段。"""
        snippets: list[CodeSnippet] = []
        source_snippet = slicer.slice_fact(source, "source")
        sink_snippet = slicer.slice_fact(sink, "sink")
        if source_snippet:
            snippets.append(source_snippet)
        if sink_snippet:
            snippets.append(sink_snippet)
        for guard in guards[:2]:
            snippet = slicer.slice_fact(guard, "guard")
            if snippet:
                snippets.append(snippet)
        for edge in path.edges[:3]:
            if edge.callsite is None:
                continue
            snippet = slicer.slice_location(
                path=edge.callsite.path,
                line=edge.callsite.line,
                end_line=edge.callsite.end_line,
                role="intermediate",
                identity=edge.edge_id,
            )
            if snippet:
                snippets.append(snippet)
        result: list[CodeSnippet] = []
        seen: set[tuple[str, int, int, str]] = set()
        for snippet in snippets:
            key = (snippet.path, snippet.start_line, snippet.end_line, snippet.role)
            if key in seen:
                continue
            seen.add(key)
            result.append(snippet)
        return result[:8]

    @staticmethod
    def _path_confidence(path: StructuralPath) -> Confidence:
        """根据边解析质量计算结构路径置信度，不代表漏洞置信度。"""
        if any(
            edge.confidence == "low" or edge.target_certainty == "unresolved"
            for edge in path.edges
        ):
            return "low"
        if any(
            edge.confidence == "medium"
            or edge.target_certainty == "may"
            or edge.dispatch in {"virtual", "dynamic"}
            for edge in path.edges
        ):
            return "medium"
        return "high"

    @staticmethod
    def _preconditions(
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        *,
        dataflow_verified: bool = False,
    ) -> list[str]:
        """生成必须由 Agent 或部署信息验证的利用前提。"""
        conditions = [] if dataflow_verified else [
            "需要验证 Source 数据是否实际传入 Sink 参数。"
        ]
        if source.trust_class == "external_configuration":
            conditions.append("攻击者需要能够影响环境变量或部署配置。")
        elif source.trust_class == "external_file":
            conditions.append("攻击者需要能够控制被读取文件的路径或内容。")
        elif source.category == "standard_input":
            conditions.append("需要验证攻击者能否向进程标准输入提供数据。")
        if sink.category == "network_request":
            conditions.append("需要验证远程目标地址是否可由攻击者控制。")
        elif sink.category == "security_sensitive_random":
            conditions.append("需要验证随机值是否用于令牌、密钥或其他安全敏感用途。")
        elif sink.category == "file_write":
            conditions.append("需要验证写入路径或写入内容是否可由攻击者控制。")
        elif sink.category == "file_path":
            conditions.append("需要验证文件访问路径是否可由攻击者控制，以及实际访问模式和权限。")
        elif sink.category == "format_string":
            conditions.append("需要验证不可信数据进入的是格式字符串本身，而非固定格式对应的数据参数。")
        conditions.extend(dict.fromkeys(
            (source.metadata.get("preconditions") or [])
            + (sink.metadata.get("preconditions") or [])
        ))
        return conditions

    @staticmethod
    def _facts_on_path(
        nodes: list[str],
        index: dict[str, list[StaticSecurityFact]],
        *,
        limit: int,
    ) -> list[StaticSecurityFact]:
        """按路径顺序选取相关事实。"""
        facts: list[StaticSecurityFact] = []
        for node in nodes:
            facts.extend(index.get(node, []))
            if len(facts) >= limit:
                break
        return facts[:limit]

    @staticmethod
    def _by_symbol(
        facts: list[StaticSecurityFact],
    ) -> dict[str, list[StaticSecurityFact]]:
        """按所属符号建立事实索引。"""
        result: dict[str, list[StaticSecurityFact]] = defaultdict(list)
        for fact in facts:
            result[fact.symbol].append(fact)
        return result

    @staticmethod
    def _sorted(facts: list[StaticSecurityFact]) -> list[StaticSecurityFact]:
        """按位置和规则稳定排序事实。"""
        return sorted(
            facts,
            key=lambda item: (
                item.location.path,
                item.location.line,
                item.location.column or 0,
                item.rule_id,
                item.fact_id,
            ),
        )

    @staticmethod
    def _sorted_entrypoints(
        entrypoints: list[SecurityEntrypoint],
    ) -> list[SecurityEntrypoint]:
        """按源码位置稳定排序程序入口。"""
        return sorted(
            entrypoints,
            key=lambda item: (
                item.location.path,
                item.location.line,
                item.symbol,
                item.entrypoint_id,
            ),
        )
