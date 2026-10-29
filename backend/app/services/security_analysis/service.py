"""静态安全结构证据的应用服务。"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from itertools import chain
from pathlib import Path
from typing import Any
from pydantic import ValidationError

from backend.app.services.program_graph import ProgramGraphService
from backend.app.core.progress import AnalysisProgressReporter, report_progress
from backend.app.services.semantic_index import (
    ProgramGraphSemanticProvider,
    SemanticIndexArtifact,
    SemanticIndexEnricher,
)
from backend.app.services.syntax_analysis.session import reuse_syntax
from backend.app.services.semantic_index.dynamic import BoundedCallableResolver

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
from .flow_analysis.diagnostics import FlowDiagnostics
from .path_finder import StructuralCallPathFinder, StructuralPath
from .scanner import SecurityScanner
from .slicer import SourceSlicer
from .candidate_search import StructuralCandidateSearch

logger = logging.getLogger(__name__)

DEFAULT_LIMITATIONS = [
    "只有标记为 intra_procedural_dataflow 或 interprocedural_dataflow 的候选包含公共 ProgramGraph 到达定义路径；其余候选只表示结构可达性。",
    "当前 CFG 尚未覆盖全部语言特殊控制语义，也未证明具体分支在运行时可行。",
    "动态派发、反射、运行时导入和未解析引用可能造成漏报或不完整路径。",
    "Guard/Sanitizer 是规则匹配观察，未经实际值、危险上下文和执行顺序验证，不作为传播截断或防护有效的证明。",
    "Source 记录已识别的输入来源，不证明攻击者可控；未确认的权限要求保持 unknown，不因可能由管理员提供而删除路径。无路径、无告警或无诊断均不证明安全或完整覆盖。",
    "当前规则包覆盖 Python/FastAPI、Java/Spring/Servlet、Go net/http/Gin/net、C/C++ 标准库/POSIX/SQLite，以及 JS/TS 的 Node.js/Express 服务端；浏览器、React/Vue 交互和 DOM 安全分析已移除，也不是全量语言/框架扫描。",
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
        max_structural_queries: int = 2_048,
        max_structural_seconds: float = 30,
        max_fact_records: int = 100_000,
    ) -> None:
        """设置候选、查询和事实存储安全预算；Source/Sink 数量只限制单批窗口。"""
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
        self.max_structural_queries = max(1, max_structural_queries)
        self.max_structural_seconds = max(0.01, max_structural_seconds)
        self.max_fact_records = max(1, max_fact_records)

    def analyze(
        self,
        *,
        project_root: str | Path,
        dependency_graph: dict[str, Any] | None = None,
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

    @reuse_syntax
    def analyze_with_semantic_index(
        self,
        *,
        project_root: str | Path,
        dependency_graph: dict[str, Any] | None = None,
        analysis_diagnostics: dict[str, Any] | None = None,
        semantic_index: dict[str, Any] | None = None,
        progress: AnalysisProgressReporter | None = None,
    ) -> tuple[SecurityEvidencePack, dict[str, Any]]:
        """生成安全证据，并返回经 ProgramGraph 补充后的语义索引。"""
        enriched_index = SemanticIndexArtifact.model_validate(semantic_index or {})
        # 新产物的文件和调用事实已独立；完整旧图仅用于旧产物兼容，不能混入另一套目标。
        if enriched_index.files is not None:
            dependency_graph = None
        report_progress(progress, "security_scan")
        scan = self.scanner.scan(project_root, dependency_graph=dependency_graph,
            **({"semantic_index": enriched_index} if enriched_index.files is not None else {}))
        if sum(map(len, (scan.sources, scan.sinks, scan.guards, scan.sanitizers))) > self.max_fact_records:
            raise ValueError("已识别安全事实超过持久化条数安全上限；请调整存储预算或缩小项目，未保存冒充完整的部分事实包。")
        flow_diagnostics = FlowDiagnostics()
        flow_stage = "program_graph"
        graph_partial = False
        try:
            report_progress(progress, "program_graph")
            program_graph = self.program_graph_service.analyze(
                project_root,
                dependency_graph=dependency_graph,
                languages=set(scan.languages_analyzed),
                **({"semantic_index": enriched_index} if enriched_index.files is not None or enriched_index.variable_declarations or enriched_index.variable_references else {}),
            )
            scan.failures.extend({"stage": "program_graph", **failure} for failure in program_graph.failures)
            graph_partial = bool(program_graph.failures)
            flow_stage = "semantic_index"
            if enriched_index.callsites:
                enriched_index, program_graph = BoundedCallableResolver().enrich(enriched_index, program_graph)
            enriched_index = self.semantic_index_enricher.enrich(
                enriched_index,
                program_graph=program_graph,
            )
            report_progress(progress, "dataflow")
            flow_stage = "dataflow"
            scan.dataflows = self.flow_analyzer.analyze(
                program_graph,
                sources=scan.sources,
                sinks=scan.sinks,
                sanitizers=scan.sanitizers,
                dependency_graph=dependency_graph,
                semantic_index=enriched_index,
                value_boundaries=scan.value_boundaries,
                diagnostics=flow_diagnostics,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            failure: dict[str, Any] = {
                "reason": "program_graph_dataflow_error",
                "detail": type(exc).__name__,
                "stage": flow_stage,
            }
            if isinstance(exc, ValidationError):
                failure["validation_errors"] = [
                    {"type": error["type"], "path": list(error["loc"])}
                    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:6]
                ]
            scan.failures.append(failure)
            logger.exception("security.dataflow_failed", extra={"stage": flow_stage})
        report_progress(progress, "evidence")
        entrypoints = self._sorted_entrypoints(scan.entrypoints)
        sources = self._sorted(scan.sources)
        for source in sources:
            source.metadata.setdefault("permission_requirement", "unknown")
        sinks = self._sorted(scan.sinks)
        guards = self._sorted(scan.guards)
        sanitizers = self._sorted(scan.sanitizers)
        limitations = list(DEFAULT_LIMITATIONS)
        if set(scan.languages_analyzed).intersection({"javascript", "typescript"}):
            limitations.append(
                "JS/TS 仅保留服务端 API、Node.js 平台及请求体 data→end 的有限绑定；浏览器、React/Vue 交互及 DOM 安全分析已移除，未建模完整回调调度、Promise 时序和堆别名。"
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
            (guards, self.max_guards, "Guard"),
            (sanitizers, self.max_sanitizers, "Sanitizer"),
        ):
            if len(values) > limit:
                limitations.append(f"{label} 关联观察最多选取 {limit} 条；全部已识别事实仍保存。优先保留值流涉及函数的观察，未附加到候选不表示不存在，也不证明防护有效。")

        # 防护关联预算不删除事实，也不作为值传播的 kill 屏障。
        flow_symbols = {step.symbol for flow in scan.dataflows for step in flow.steps}
        retained_guards = sorted(guards, key=lambda item: item.symbol not in flow_symbols)[:self.max_guards]
        retained_sanitizers = sorted(sanitizers, key=lambda item: item.symbol not in flow_symbols)[:self.max_sanitizers]
        path_finder = StructuralCallPathFinder(
            dependency_graph,
            max_depth=self.max_path_depth,
            semantic_index=enriched_index,
        )
        slicer = SourceSlicer(project_root)
        diagnostics = analysis_diagnostics or {}
        resolved_dynamic = {key for key, call in enriched_index.callsites.items()
                            if any(target.resolution_method == "bounded_callable_value" for target in call.targets)}
        unresolved = [item for item in diagnostics.get("unresolved_references", [])
                      if item.get("callsite_id") not in resolved_dynamic]
        unresolved.extend({"callsite_id": call.callsite_id, "from_fqn": call.caller_symbol,
            "file": call.location.path, "line": call.location.line,
            "unresolved_reason": reason, "target_certainty": "may"}
            for call in enriched_index.callsites.values() for reason in call.binding_diagnostics)
        guards_by_symbol = self._by_symbol(retained_guards)
        sanitizers_by_symbol = self._by_symbol(retained_sanitizers)
        candidates: list[SecurityCandidate] = []
        call_edges = {}
        snippets = {}
        dataflows = {item.flow_id: item for item in scan.dataflows}
        # 先覆盖已有值流端点族，再轮询变体，剩余预算给结构线索；不让低精度线索挤掉已建链。
        dataflows_by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for item in sorted(scan.dataflows, key=lambda flow: (
            {"satisfiable_under_model": 0, "unknown": 1, "not_checked": 2, "infeasible_under_model": 3}.get(
                flow.path_conditions[0].status if flow.path_conditions else "not_checked", 2), len(flow.steps), flow.flow_id)):
            dataflows_by_pair[(item.source_fact_id, item.sink_fact_id)].append(item)
        candidate_limit_reached = False
        search = StructuralCandidateSearch(path_finder, sources, sinks,
            source_window=self.max_sources, sink_window=self.max_sinks,
            max_queries=self.max_structural_queries, max_seconds=self.max_structural_seconds)
        source_order = {item.fact_id: index for index, item in enumerate(sources)}
        sink_order = {item.fact_id: index for index, item in enumerate(sinks)}
        preferred_pairs = sorted((source_order[left], sink_order[right]) for left, right in dataflows_by_pair
            if left in source_order and right in sink_order)
        first = [(sources[left], sinks[right], dataflows_by_pair[
            (sources[left].fact_id, sinks[right].fact_id)][0]) for left, right in preferred_pairs]
        alternatives = ((sources[left], sinks[right], values[index])
            for index in range(1, max((len(values) for values in dataflows_by_pair.values()), default=1))
            for left, right in preferred_pairs
            for values in (dataflows_by_pair[(sources[left].fact_id, sinks[right].fact_id)],)
            if index < len(values))
        structural_alternatives: list[tuple[StaticSecurityFact, StaticSecurityFact, StructuralPath]] = []
        # 包装标记统一流/结构两类输入，避免重复搜索同一源函数与目标窗口。
        preferred = ((source, sink, dataflow, None) for source, sink, dataflow in chain(first, alternatives))
        structural = ((source, sink, None, paths) for source, sink, paths in search.pairs(set(dataflows_by_pair)))
        for source, sink, dataflow, paths in chain(preferred, structural):
            if dataflow is not None:
                path = StructuralPath(nodes=list(dict.fromkeys(step.symbol for step in dataflow.steps)),
                                      edges=path_finder.edges(dataflow.call_edge_ids))
            else:
                path = paths[0] if paths else None
                structural_alternatives.extend((source, sink, item) for item in paths[1:])
            if path is None:
                continue
            if len(candidates) >= self.max_candidates:
                candidate_limit_reached = True
                break
            candidate, candidate_snippets = self._candidate(
                source=source, sink=sink, path=path,
                guards_by_symbol=guards_by_symbol, sanitizers_by_symbol=sanitizers_by_symbol,
                unresolved=unresolved, slicer=slicer, dataflow=dataflow,
            )
            candidates.append(candidate)
            for edge in path.edges:
                call_edges.setdefault(edge.edge_id, edge)
            for snippet in candidate_snippets:
                snippets.setdefault(snippet.snippet_id, snippet)

        for source, sink, path in structural_alternatives:
            if len(candidates) >= self.max_candidates:
                candidate_limit_reached = True
                break
            candidate, candidate_snippets = self._candidate(source=source, sink=sink, path=path,
                guards_by_symbol=guards_by_symbol, sanitizers_by_symbol=sanitizers_by_symbol,
                unresolved=unresolved, slicer=slicer, dataflow=None)
            candidates.append(candidate)
            call_edges.update((edge.edge_id, edge) for edge in path.edges)
            snippets.update((snippet.snippet_id, snippet) for snippet in candidate_snippets)

        if candidate_limit_reached:
            search.reason = "candidate_budget"
            limitations.append(
                f"安全候选达到 {self.max_candidates} 条上限；完整计数需要提高预算后重新分析。"
            )
        elif search.reason:
            limitations.append("结构候选查询因 " + search.reason + " 结束；未遍历窗口不表示不存在路径，已识别事实和已建立值流仍完整保存。")
        retained_flow_ids = {item.dataflow_id for item in candidates if item.dataflow_id}
        omitted_flow_count = len(dataflows.keys() - retained_flow_ids)
        if omitted_flow_count:
            flow_diagnostics.record("established_flows_outside_candidate_budget", count=omitted_flow_count,
                                    candidate_limit=self.max_candidates)
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
            facts_complete=True,
            candidate_source_count=len({item.source_fact_id for item in candidates}),
            candidate_sink_count=len({item.sink_fact_id for item in candidates}),
            flows_without_candidate=omitted_flow_count,
            candidate_search=search.coverage(),
            dataflow_status=("failed" if any(item.get("reason") == "program_graph_dataflow_error" for item in scan.failures)
                             else "partial" if graph_partial else "completed"),
        )
        retained_facts = (
            sources + sinks + guards + sanitizers
        )
        # 即使没有 Source→Sink 候选，也保存已观察输入/危险 API 的位置与脱敏片段。
        snippet_fact_ids = {item.source_fact_id for item in candidates} | {item.sink_fact_id for item in candidates}
        snippet_fact_ids.update(item.fact_id for item in sources[:self.max_sources] + sinks[:self.max_sinks])
        for fact in sources + sinks:
            if fact.fact_id not in snippet_fact_ids:
                continue
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
            completed=coverage.dataflow_status != "failed",
            scan_failures=scan.failures,
            flow_diagnostics=flow_diagnostics.as_dict(),
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
        material = f"{source.fact_id}\x1f{sink.fact_id}\x1f{edge_ids}\x1f{dataflow.flow_id if dataflow else 'structural'}"
        candidate_id = "candidate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        truncated = (
            unresolved_truncated or path.truncated
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
                limitations.append("本路径包含有静态依据的 may 值边界，与既有调用边分开记录；不证明运行路径可行或完整对象、事件及重渲染生命周期。")
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
            path_group_id="pathgroup:" + hashlib.sha256(f"{source.fact_id}|{sink.fact_id}".encode()).hexdigest()[:24],
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
            truncated=truncated or bool(dataflow and dataflow.truncated),
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
            )
            if snippet:
                snippets.append(snippet)
        return list({snippet.snippet_id: snippet for snippet in snippets}.values())[:8]

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
