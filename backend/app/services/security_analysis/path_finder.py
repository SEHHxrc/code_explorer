"""在服务器内部无损图上计算有界结构调用路径。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.semantic_index.projection import ProgramFacts
from backend.app.services.semantic_index.contracts import SemanticIndexArtifact

from .contracts import Confidence, EvidenceLocation, PathEdgeEvidence, TargetCertainty


@dataclass(frozen=True)
class StructuralPath:
    """一次有界图搜索得到的节点和调用边序列。"""

    nodes: list[str]
    edges: list[PathEdgeEvidence]
    truncated: bool = False


class StructuralCallPathFinder:
    """只搜索 ``calls`` 边，不把结构可达性解释为变量级数据流。"""

    def __init__(self, graph: dict[str, Any] | None = None, *, max_depth: int = 6,
                 semantic_index: SemanticIndexArtifact | dict[str, Any] | None = None) -> None:
        """输入 NetworkX node-link 图和最大调用深度，构建稳定邻接索引。"""
        self.max_depth = max(1, max_depth)
        self.adjacency: dict[str, list[tuple[str, PathEdgeEvidence]]] = defaultdict(list)
        self._edges_by_id: dict[str, PathEdgeEvidence] = {}
        self._cache: dict[tuple[str, str], StructuralPath | None] = {}
        self.reverse: dict[str, set[str]] = defaultdict(set)
        self._relevant_cache: dict[frozenset[str], set[str]] = {}
        raw_edges = ProgramFacts(semantic_index, graph).call_edges()
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, dict):
                continue
            relation = str(raw_edge.get("relation") or raw_edge.get("type") or "calls")
            if relation not in {"calls", "instantiates"}:
                continue
            source = self._endpoint(raw_edge.get("source"))
            target = self._endpoint(raw_edge.get("target"))
            if not source or not target:
                continue
            evidence = self.edge_evidence(source, target, raw_edge)
            if relation == "calls":
                self.adjacency[source].append((target, evidence))
                self.reverse[target].add(source)
            self._edges_by_id[evidence.edge_id] = evidence
        for edges in self.adjacency.values():
            edges.sort(key=lambda item: (item[0], item[1].edge_id))

    def edges(self, edge_ids: list[str]) -> list[PathEdgeEvidence]:
        """按输入顺序返回已经规范化的调用边证据。"""
        return [self._edges_by_id[item] for item in edge_ids if item in self._edges_by_id]

    def find(self, source: str, target: str) -> StructuralPath | None:
        """返回不超过深度上限的最短结构调用路径；不可达时返回 ``None``。"""
        if not source or not target:
            return None
        cache_key = (source, target)
        if cache_key in self._cache:
            return self._cache[cache_key]
        return self.find_reachable(source, {target}).get(target)

    def find_reachable(self, source: str, targets: set[str]) -> dict[str, StructuralPath]:
        """兼容单路线调用者，仍复用同一个有界搜索；未找到不表示运行时不可达。"""
        paths = self.find_variants(source, targets, max_variants=1)
        result = {target: values[0] for target, values in paths.items() if values}
        for target in targets:
            self._cache[(source, target)] = result.get(target)
        return result

    def find_variants(self, source: str, targets: set[str], *, max_variants: int = 8,
                      max_states: int = 2048) -> dict[str, list[StructuralPath]]:
        """一次 Source 查询保留不同调用边序列；节点不重复，深度/状态/数量上限均明确标记。"""
        if min(max_variants, max_states) < 1:
            raise ValueError("结构路径预算必须为正数")
        if not source or not targets:
            return {}
        # 先反向裁剪不能到达任一目标的区域，不为每个 Source/Sink 对重跑整图。
        target_key = frozenset(targets)
        relevant = self._relevant_cache.get(target_key)
        if relevant is None:
            relevant, pending = set(targets), list(targets)
            while pending:
                for caller in self.reverse.get(pending.pop(), ()):
                    if caller not in relevant:
                        relevant.add(caller)
                        pending.append(caller)
            # 同一目标窗口复用反向裁剪；缓存最多两组，不随项目查询数增长。
            if len(self._relevant_cache) >= 2:
                self._relevant_cache.pop(next(iter(self._relevant_cache)))
            self._relevant_cache[target_key] = relevant
        if source not in relevant:
            return {}
        results: dict[str, list[StructuralPath]] = defaultdict(list)
        queue = deque([StructuralPath(nodes=[source], edges=[])])
        explored, truncated = 0, False
        while queue:
            if explored >= max_states:
                truncated = True
                break
            path = queue.popleft()
            explored += 1
            current = path.nodes[-1]
            if current in targets:
                if len(results[current]) < max_variants:
                    results[current].append(path)
                else:
                    truncated = True
            if len(path.edges) >= self.max_depth:
                truncated = truncated or any(target in relevant for target, _ in self.adjacency.get(current, ()))
                continue
            for target, edge in self.adjacency.get(current, ()):
                if target not in relevant:
                    continue
                if target in path.nodes:
                    truncated = True
                    continue
                if len(queue) + explored >= max_states:
                    truncated = True
                    break
                queue.append(StructuralPath(nodes=[*path.nodes, target], edges=[*path.edges, edge]))
        return {target: [StructuralPath(nodes=path.nodes, edges=path.edges, truncated=truncated)
                         for path in values] for target, values in results.items()}

    @classmethod
    def edge_evidence(
        cls,
        source: str,
        target: str,
        raw_edge: dict[str, Any],
    ) -> PathEdgeEvidence:
        """把原始多重图边转换为安全证据路径边。"""
        callsite = raw_edge.get("callsite") or {}
        location = None
        path = str(callsite.get("path") or "")
        line = cls._positive_int(callsite.get("line"))
        if path and line:
            location = EvidenceLocation(
                path=path,
                line=line,
                column=cls._positive_int(callsite.get("column")),
                end_line=cls._positive_int(callsite.get("end_line")),
                end_column=cls._positive_int(callsite.get("end_column")),
                location_id=str(callsite.get("location_id") or "") or None,
            )
        callsite_id = str(raw_edge.get("callsite_id") or callsite.get("id") or "")
        if not callsite_id and path and line:
            callsite_id = ProgramIdentity.callsite_id(
                path,
                line,
                cls._positive_int(callsite.get("column")) or 0,
                cls._positive_int(callsite.get("end_line")) or line,
                cls._positive_int(callsite.get("end_column")) or 0,
            )
        edge_id = str(raw_edge.get("id") or "")
        if not edge_id:
            material = "\x1f".join((source, target, str(line or 0), str(raw_edge.get("key") or "")))
            edge_id = "edge:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        if not callsite_id:
            callsite_id = "callsite:legacy:" + hashlib.sha256(
                edge_id.encode("utf-8")
            ).hexdigest()[:24]
        raw_certainty = str(raw_edge.get("target_certainty") or "unresolved")
        certainty: TargetCertainty
        if raw_certainty == "must":
            certainty = "must"
        elif raw_certainty == "may":
            certainty = "may"
        else:
            certainty = "unresolved"
        raw_confidence = str(raw_edge.get("confidence") or "low")
        confidence: Confidence
        if raw_confidence == "high":
            confidence = "high"
        elif raw_confidence == "medium":
            confidence = "medium"
        else:
            confidence = "low"
        return PathEdgeEvidence(
            edge_id=edge_id,
            callsite_id=callsite_id,
            source=source,
            target=target,
            relation=str(raw_edge.get("relation") or "calls"),
            dispatch=str(raw_edge.get("dispatch") or "unknown"),
            resolution_method=str(raw_edge.get("resolution_method") or "unknown"),
            target_certainty=certainty,
            confidence=confidence,
            truncated=bool(raw_edge.get("truncated")),
            unresolved_reason=str(raw_edge.get("unresolved_reason") or "") or None,
            fallback_reason=str(raw_edge.get("fallback_reason") or "") or None,
            provenance=(
                "observed" if raw_edge.get("origin") == "observed" else "inferred"
            ),
            callsite=location,
        )

    @staticmethod
    def _endpoint(value: Any) -> str:
        """读取 node-link 边端点标识。"""
        if isinstance(value, dict):
            value = value.get("id")
        return "" if value is None else str(value)

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        """把任意输入转换为正整数。"""
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
