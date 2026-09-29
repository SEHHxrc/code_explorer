"""在服务器内部无损图上计算有界结构调用路径。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from backend.app.services.program_index import ProgramIdentity

from .contracts import Confidence, EvidenceLocation, PathEdgeEvidence, TargetCertainty


@dataclass(frozen=True)
class StructuralPath:
    """一次有界图搜索得到的节点和调用边序列。"""

    nodes: list[str]
    edges: list[PathEdgeEvidence]


class StructuralCallPathFinder:
    """只搜索 ``calls`` 边，不把结构可达性解释为变量级数据流。"""

    def __init__(self, graph: dict[str, Any], *, max_depth: int = 6) -> None:
        """输入 NetworkX node-link 图和最大调用深度，构建稳定邻接索引。"""
        self.max_depth = max(1, max_depth)
        self.adjacency: dict[str, list[tuple[str, PathEdgeEvidence]]] = defaultdict(list)
        self._edges_by_id: dict[str, PathEdgeEvidence] = {}
        self._cache: dict[tuple[str, str], StructuralPath | None] = {}
        raw_edges = graph.get("links", graph.get("edges", [])) or []
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, dict):
                continue
            if str(raw_edge.get("relation") or raw_edge.get("type") or "calls") != "calls":
                continue
            source = self._endpoint(raw_edge.get("source"))
            target = self._endpoint(raw_edge.get("target"))
            if not source or not target:
                continue
            evidence = self._edge_evidence(source, target, raw_edge)
            self.adjacency[source].append((target, evidence))
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

    def find_reachable(
        self,
        source: str,
        targets: set[str],
    ) -> dict[str, StructuralPath]:
        """一次 BFS 返回指定 Source 可达的全部目标，避免逐候选重复遍历图。"""
        pending = {target for target in targets if target}
        results: dict[str, StructuralPath] = {}
        if not source or not pending:
            return results
        if source in pending:
            results[source] = StructuralPath(nodes=[source], edges=[])
            self._cache[(source, source)] = results[source]
            pending.remove(source)
        cached_targets = list(pending)
        for target in cached_targets:
            cache_key = (source, target)
            if cache_key not in self._cache:
                continue
            cached = self._cache[cache_key]
            if cached is not None:
                results[target] = cached
            pending.remove(target)
        if not pending:
            return results

        queue = deque([source])
        depth: dict[str, int] = {source: 0}
        predecessor: dict[str, tuple[str, PathEdgeEvidence]] = {}
        while queue and pending:
            current = queue.popleft()
            current_depth = depth[current]
            if current_depth >= self.max_depth:
                continue
            for next_node, edge in self.adjacency.get(current, []):
                if next_node in depth:
                    continue
                predecessor[next_node] = (current, edge)
                depth[next_node] = current_depth + 1
                if next_node in pending:
                    path = self._reconstruct(source, next_node, predecessor)
                    results[next_node] = path
                    self._cache[(source, next_node)] = path
                    pending.remove(next_node)
                queue.append(next_node)
        for target in pending:
            self._cache[(source, target)] = None
        return results

    @staticmethod
    def _reconstruct(
        source: str,
        target: str,
        predecessor: dict[str, tuple[str, PathEdgeEvidence]],
    ) -> StructuralPath:
        """根据 BFS 前驱表重建节点和边序列。"""
        reversed_nodes = [target]
        reversed_edges: list[PathEdgeEvidence] = []
        current = target
        while current != source:
            previous, edge = predecessor[current]
            reversed_edges.append(edge)
            reversed_nodes.append(previous)
            current = previous
        return StructuralPath(
            nodes=list(reversed(reversed_nodes)),
            edges=list(reversed(reversed_edges)),
        )

    @classmethod
    def _edge_evidence(
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
