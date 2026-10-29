"""把持久化安全候选和独立操作投影到函数图；不重扫、不补造传播、不调用模型。"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from typing import Any

from backend.app.schemas.security_paths import (
    PathEndpointDTO, PathLocationDTO, SecurityPathDTO, SecurityPathDetailDTO,
    SecurityOperationDTO, SecurityPathGraphDTO, SecurityPathStepDTO,
    SecurityBoundaryDTO, SecurityCallConnectionDTO,
)
from backend.app.services.security_analysis.contracts import (
    EvidenceLocation, PathEdgeEvidence, SecurityCandidate, SecurityEvidencePack, StaticSecurityFact,
)
from backend.app.services.security_analysis.evidence_selection import SecurityEvidenceSelector
from backend.app.services.security_analysis.path_finder import StructuralCallPathFinder
from backend.app.services.semantic_index.projection import ProgramFacts
from backend.app.services.semantic_index.contracts import SemanticLocation

from .graph_exchange import GraphExchangeNormalizer
from .structure_context import incoming_context

BOUNDARY_REASONS = {"source_to_operation_not_established", "mapping_keys_or_duplicates_unknown",
    "dynamic_member_name_unknown", "dynamic_receiver_type_unknown", "dynamic_callable_binding_unknown", "dynamic_target_limit",
    "dynamic_bound_receiver_changed", "duplicate_keyword_argument"}


class SecurityPathProjection:
    """复用路径目录的身份、分组与分类，单独负责展示投影和安全字段过滤。"""

    def __init__(self, artifact: dict[str, Any], normalizer: GraphExchangeNormalizer | None = None) -> None:
        """输入已读取的项目产物及可选交换规范化器，不访问项目文件。"""
        raw_graph = artifact.get("dependency_graph") or {}
        if not isinstance(raw_graph, dict) or not isinstance(raw_graph.get("nodes", []), list):
            raise ValueError("依赖图产物格式不正确。")
        raw = artifact.get("security_evidence")
        self.facts = ProgramFacts(artifact.get("semantic_index"), raw_graph)
        pack = SecurityEvidencePack.model_validate(raw or {})
        self.candidate_count = len(pack.candidates)
        represented = {candidate.dataflow_id for candidate in pack.candidates}
        extra_flows = [flow for key, flow in pack.dataflows.items() if key not in represented]
        valid_flows = [flow for flow in extra_flows if flow.source_fact_id in pack.facts and flow.sink_fact_id in pack.facts]
        self.additional_dataflow_count = len(valid_flows)
        self.unavailable_dataflows = len(extra_flows) - len(valid_flows)
        # 展示不能继承 LLM 候选预算：补入已有值流引用，不生成新的传播路径，不改原产物。
        additional = [SecurityCandidate(
            candidate_id="display-flow:" + hashlib.sha256(flow.flow_id.encode()).hexdigest()[:24],
            rule_id=pack.facts[flow.sink_fact_id].rule_id, category=pack.facts[flow.sink_fact_id].category,
            severity=pack.facts[flow.sink_fact_id].severity, confidence=flow.confidence,
            source_fact_id=flow.source_fact_id, sink_fact_id=flow.sink_fact_id, dataflow_id=flow.flow_id,
            path_kind="interprocedural_dataflow" if flow.scope == "interprocedural" else "intra_procedural_dataflow",
            dataflow_verified=True, taint_status=flow.status, call_edge_ids=flow.call_edge_ids,
            truncated=flow.truncated) for flow in valid_flows]
        call_edges = dict(pack.call_edges)
        missing_edges = list({key for flow in valid_flows for key in flow.call_edge_ids if key not in call_edges})
        if missing_edges:
            # 仅恢复原 ID 对应的真实关系，不运行路径搜索或依据步骤顺序猜边。
            finder = StructuralCallPathFinder(raw_graph, semantic_index=artifact.get("semantic_index"))
            call_edges.update((edge.edge_id, edge) for edge in finder.edges(missing_edges))
        self.selector = SecurityEvidenceSelector(pack.model_copy(update={
            "candidates": [*pack.candidates, *additional], "call_edges": call_edges, "snippets": {}}))
        self.available = isinstance(raw, dict) and bool(raw)
        self.pack = self.selector.pack
        self.operations = {row["id"]: self.pack.facts[row["id"]] for row in self.selector.rows()
                           if row["id"] not in self.selector.candidates}
        self.normalizer = normalizer or GraphExchangeNormalizer()
        self.raw_nodes = {str(node["id"]): node for node in self.facts.nodes()}
        self.incoming, self.structure_truncated = incoming_context(self.facts, {fact.symbol for fact in self.operations.values()})
        self.boundaries: dict[str, SecurityBoundaryDTO] = {}
        self.binding_calls = defaultdict(list)
        for call in self.facts.index.callsites.values():
            if call.binding_diagnostics:
                self.binding_calls[call.caller_symbol].append(call)
        self.operation_views: dict[str, SecurityOperationDTO] = {}
        self.connection_views: dict[str, list[PathEdgeEvidence]] = {}

    @staticmethod
    def location(location: EvidenceLocation | SemanticLocation | None) -> PathLocationDTO | None:
        """输入内部位置，仅输出安全相对路径和有效行范围；不暴露绝对路径。"""
        if location is None or location.line < 1:
            return None
        path = GraphExchangeNormalizer._safe_relative_path(location.path)
        if not path:
            return None
        return PathLocationDTO(path=path, line=location.line, column=location.column or None,
            end_line=max(location.line, location.end_line) if location.end_line else None,
            end_column=location.end_column or None)

    @classmethod
    def endpoint(cls, fact: StaticSecurityFact) -> PathEndpointDTO:
        """输入 Source/Sink/防护事实，输出名称、身份、信任类别与安全源码位置。"""
        return PathEndpointDTO(name=GraphExchangeNormalizer._text(fact.name, 500),
            symbol=cls.safe_symbol(fact.symbol), location=cls.location(fact.location), trust_class=fact.trust_class)

    @staticmethod
    def safe_symbol(symbol: str) -> str:
        """输入内部符号标识；拒绝以宿主绝对路径或父级跳转为前缀的身份。"""
        return GraphExchangeNormalizer._text(symbol, 1000) if GraphExchangeNormalizer._safe_relative_path(
            symbol.split("::", 1)[0]) else ""

    @classmethod
    def connection(cls, edge: PathEdgeEvidence) -> SecurityCallConnectionDTO:
        """将已有调用依据投影为安全详情；与摘要复用同一个状态判定。"""
        return SecurityCallConnectionDTO(call_edge_id=edge.edge_id, source=cls.safe_symbol(edge.source),
            target=cls.safe_symbol(edge.target), status=edge.connection_status, target_certainty=edge.target_certainty,
            confidence=edge.confidence, resolution_method=GraphExchangeNormalizer._text(edge.resolution_method, 80),
            location=cls.location(edge.callsite), truncated=edge.truncated, speculative_target=edge.is_speculative_target)

    def build(self) -> SecurityPathGraphDTO:
        """输出候选并集与独立操作节点；只使用已有调用关系，未配对操作不补 Source 或连线。"""
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        paths: list[SecurityPathDTO] = []
        operations: list[SecurityOperationDTO] = []
        connections: dict[str, PathEdgeEvidence] = {}
        context_connection_ids: set[str] = set()
        self.boundaries = {}

        def add_node(symbol: str, location: EvidenceLocation | None = None) -> str:
            """保留原节点身份；缺失声明时只使用事实符号和文件，不伪造声明行号。"""
            symbol = self.safe_symbol(symbol)
            if not symbol:
                return ""
            raw = self.raw_nodes.get(symbol)
            if symbol not in nodes:
                point = self.location(location)
                node = raw or {"id": symbol, "name": symbol.rsplit("::", 1)[-1],
                    "kind": "external" if symbol.startswith("<") else "function",
                    "level": "external" if symbol.startswith("<") else "function",
                    "file": point.path if point else None}
                # 类限定名来自既有符号身份；避免所有 Provider 方法都只显示为 _query。
                parts = symbol.split("::")[1:]
                name = ".".join(parts[-2:]) if len(parts) > 1 and node.get("name") == parts[-1] else node.get("name")
                nodes[symbol] = {**node, "name": name}
            return symbol

        def boundary(symbol: str, location: EvidenceLocation | SemanticLocation, reasons: list[str]) -> str:
            """按真实位置去重，过滤宿主路径和自由文本，不推测断点另一端。"""
            safe, point = self.safe_symbol(symbol), self.location(location)
            reasons = [reason for reason in reasons if reason in BOUNDARY_REASONS]
            identifier = "boundary:" + hashlib.sha256((safe + str(point) + str(reasons)).encode()).hexdigest()[:24]
            self.boundaries[identifier] = SecurityBoundaryDTO(id=identifier, symbol=safe, location=point,
                reasons=reasons)
            return identifier

        for row in self.selector.rows():
            candidate = self.selector.candidates.get(row["id"])
            if candidate is None:
                fact = self.operations[row["id"]]
                symbol = add_node(fact.symbol, fact.location)
                operations.append(SecurityOperationDTO(id=fact.fact_id, category=fact.category,
                    rule_id=fact.rule_id, severity=fact.severity, sink=self.endpoint(fact), node_id=symbol,
                    projection_missing=int(not symbol) + int(self.location(fact.location) is None)))
                operation = operations[-1]
                operation.boundary_ids.append(boundary(fact.symbol, fact.location, ["source_to_operation_not_established"]))
                frontier, visited = [fact.symbol], set()
                for _ in range(2):
                    following = []
                    for target in frontier:
                        visited.add(target)
                        for edge in self.incoming.get(target, []):
                            left, right = add_node(str(edge["source"])), add_node(str(edge["target"]))
                            key = str(edge.get("id", ""))
                            if not left or not right or not key:
                                continue
                            edges.setdefault(key, {"id": key, "source": left, "target": right,
                                "relation": "calls", "dispatch": str(edge.get("dispatch", ""))})
                            connections[key] = StructuralCallPathFinder.edge_evidence(left, right, edge)
                            context_connection_ids.add(key)
                            operation.context_edge_ids.append(key)
                            following.append(str(edge["source"]))
                    frontier = [key for key in following if key not in visited]
                operation.context_node_ids = list(filter(None, (add_node(key) for key in sorted(visited | set(frontier)))))
                for owner in visited:
                    for call in self.binding_calls[owner]:
                        operation.boundary_ids.append(boundary(call.caller_symbol, call.location, call.binding_diagnostics))
                continue
            source, sink = self.pack.facts[candidate.source_fact_id], self.pack.facts[candidate.sink_fact_id]
            symbols = [add_node(source.symbol, source.location)]
            flow = self.pack.dataflows.get(candidate.dataflow_id or "")
            if flow:
                symbols.extend(add_node(step.symbol, step.location) for step in flow.steps)
            edge_ids, missing = [], sum(not symbol for symbol in symbols)
            for edge_id in dict.fromkeys([*candidate.call_edge_ids, *(flow.call_edge_ids if flow else [])]):
                edge = self.pack.call_edges.get(edge_id)
                if edge is None:
                    missing += 1
                    continue
                left, right = add_node(edge.source, edge.callsite), add_node(edge.target)
                symbols.extend((left, right))
                if not left or not right:
                    missing += 1
                elif left != right:  # 递归/函数内传播保留在步骤详情，不画虚假自环。
                    edges[edge_id] = {"id": edge_id, "source": left, "target": right,
                                      "relation": edge.relation, "dispatch": edge.dispatch}
                    connections[edge_id] = edge
                    edge_ids.append(edge_id)
            symbols.append(add_node(sink.symbol, sink.location))
            paths.append(SecurityPathDTO(id=candidate.candidate_id, path_group_id=self.selector.group_ids[candidate.candidate_id],
                kind=row["kind"], category=candidate.category, confidence=candidate.confidence,
                source=self.endpoint(source), sink=self.endpoint(sink), node_ids=list(dict.fromkeys(filter(None, symbols))),
                edge_ids=edge_ids, step_count=row["step_count"], path_status=row["path_status"],
                possible_edge_ids=[key for key in edge_ids if connections[key].is_speculative_target],
                call_connection_counts=row.get("call_connection_counts", {}),
                condition_statuses=row.get("path_condition_status", []), unresolved_count=row["unresolved_count"],
                truncated=candidate.truncated or bool(flow and flow.truncated),
                projection_missing=missing + int(not symbols[-1])
                    + int(self.location(source.location) is None) + int(self.location(sink.location) is None)))

        dynamic_sites = [call for calls in self.binding_calls.values() for call in calls
                         if any(reason.startswith("dynamic_") for reason in call.binding_diagnostics)]
        for call in dynamic_sites[:128]:
            add_node(call.caller_symbol)
            boundary(call.caller_symbol, call.location, call.binding_diagnostics)
        graph = self.normalizer.normalize({"nodes": list(nodes.values()), "edges": list(edges.values())})
        retained_nodes = {node.id for node in graph.nodes}
        # 交换层会合并同关系/派发的重复调用点；映射所有原 ID，不能丢掉其他路线的成员关系。
        retained_edges = {(edge.source, edge.target, edge.relation, edge.dispatch or ""): edge.id for edge in graph.edges}

        def retained_id(key: str) -> str | None:
            """仅映射交换层实际保留的边，不通过节点邻接重新构造关系。"""
            edge = edges[key]
            return retained_edges.get((edge["source"], edge["target"], edge["relation"], edge["dispatch"]))

        self.connection_views = defaultdict(list)
        possible_edges = set()
        for key, evidence in connections.items():
            mapped_id = retained_id(key)
            if mapped_id is not None:
                if key in context_connection_ids:
                    self.connection_views[mapped_id].append(evidence)
                if evidence.is_speculative_target:
                    possible_edges.add(mapped_id)
        for path in paths:
            mapped = [retained_id(key) for key in path.edge_ids]
            path.projection_missing += sum(key not in retained_nodes for key in path.node_ids) + mapped.count(None)
            path.node_ids = [key for key in path.node_ids if key in retained_nodes]
            path.edge_ids = list(dict.fromkeys(filter(None, mapped)))
            path.possible_edge_ids = list(dict.fromkeys(filter(None, (retained_id(key) for key in path.possible_edge_ids))))
        for operation in operations:
            if operation.node_id and operation.node_id not in retained_nodes:
                operation.projection_missing += 1
                operation.node_id = ""
            operation.context_node_ids = [key for key in operation.context_node_ids if key in retained_nodes]
            operation.context_edge_ids = list(dict.fromkeys(filter(None, (
                retained_edges.get((edges[key]["source"], edges[key]["target"], "calls", edges[key]["dispatch"]))
                for key in operation.context_edge_ids))))
        self.operation_views = {operation.id: operation for operation in operations}
        route_edges = {key for path in paths for key in path.edge_ids}
        context_edges = {key for operation in operations for key in operation.context_edge_ids}
        coverage = self.pack.coverage
        return SecurityPathGraphDTO(available=self.available, completed=self.available and self.pack.completed,
            graph=graph, paths=paths, operations=operations, candidate_count=self.candidate_count,
            additional_dataflow_count=self.additional_dataflow_count, unavailable_dataflows=self.unavailable_dataflows,
            unavailable_candidates=self.selector.unavailable, kind_counts=dict(Counter(path.kind for path in paths)),
            unpaired_sink_count=len(operations),
            analysis_truncated=coverage.candidate_limit_reached or any(path.truncated for path in paths),
            scan_failure_count=len(self.pack.scan_failures), parse_failure_count=coverage.parse_failures,
            files_scanned=coverage.files_scanned, files_considered=coverage.files_considered,
            unsupported_languages=self.pack.unsupported_languages,
            structural_edge_ids=sorted(context_edges - route_edges), possible_edge_ids=sorted(possible_edges),
            boundaries=list(self.boundaries.values()),
            structure_truncated=self.structure_truncated or len(dynamic_sites) > 128)

    def detail(self, candidate_id: str) -> SecurityPathDetailDTO:
        """输入候选或独立操作 ID，返回已保存定位；未知 ID 抛 KeyError，不执行分析或模型选择。"""
        if candidate_id in self.operations:
            if candidate_id not in self.operation_views:
                self.build()
            fact = self.operations[candidate_id]
            point = self.location(fact.location)
            return SecurityPathDetailDTO(id=candidate_id,
                steps=[SecurityPathStepDTO(kind="sink", symbol=self.safe_symbol(fact.symbol), location=point)],
                unavailable_locations=int(point is None),
                call_connections=[self.connection(item) for key in self.operation_views[candidate_id].context_edge_ids
                                  for item in self.connection_views.get(key, [])],
                boundaries=[self.boundaries[key] for key in self.operation_views[candidate_id].boundary_ids])
        candidate = self.selector.candidates[candidate_id]
        flow = self.pack.dataflows.get(candidate.dataflow_id or "")
        steps: list[SecurityPathStepDTO] = []
        if flow:
            steps = [SecurityPathStepDTO(kind=step.kind, symbol=self.safe_symbol(step.symbol),
                location=self.location(step.location), uses=step.input_names, defines=step.output_names,
                certainty=step.certainty) for step in flow.steps]
        else:
            steps = [SecurityPathStepDTO(kind=edge.relation, symbol=self.safe_symbol(edge.source),
                location=self.location(edge.callsite), certainty=edge.target_certainty)
                for key in candidate.call_edge_ids if (edge := self.pack.call_edges.get(key)) is not None]
        return SecurityPathDetailDTO(id=candidate_id, steps=steps,
            call_connections=[self.connection(self.pack.call_edges[key])
                for key in dict.fromkeys([*candidate.call_edge_ids, *(flow.call_edge_ids if flow else [])])
                if key in self.pack.call_edges],
            guards=[self.endpoint(self.pack.facts[key]) for key in candidate.guard_fact_ids if key in self.pack.facts],
            sanitizers=[self.endpoint(self.pack.facts[key]) for key in candidate.sanitizer_fact_ids if key in self.pack.facts],
            value_boundary_count=len(flow.value_boundary_ids) if flow else 0,
            unavailable_locations=sum(step.location is None for step in steps))
