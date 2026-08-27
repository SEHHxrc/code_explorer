"""将分析器内部图转换为稳定、安全的前端交换图。"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from backend.app.schemas.dependency_graph import (
    DependencyGraphDTO,
    GraphEdgeDTO,
    GraphNodeDTO,
    GraphSummaryDTO,
)


class GraphExchangeNormalizer:
    """对白名单字段、路径、边端点、重复项和体量做统一规范化。"""

    def __init__(self, max_nodes: int = 50_000, max_edges: int = 200_000):
        self.max_nodes = max_nodes
        self.max_edges = max_edges

    def normalize(self, raw_graph: dict[str, Any] | None) -> DependencyGraphDTO:
        """输入分析器原始图，输出可安全跨越 HTTP 边界的版本化 DTO。"""
        raw_graph = raw_graph if isinstance(raw_graph, dict) else {}
        raw_nodes = raw_graph.get("nodes") or []
        raw_edges = raw_graph.get("edges") or raw_graph.get("links") or []
        warnings: list[str] = []
        skipped_nodes = 0
        duplicate_nodes = 0
        nodes: list[GraphNodeDTO] = []
        node_ids: set[str] = set()

        for raw_node in raw_nodes[: self.max_nodes]:
            if not isinstance(raw_node, dict):
                skipped_nodes += 1
                continue
            node_id = self._text(raw_node.get("id"), 1000)
            if not node_id:
                skipped_nodes += 1
                continue
            if node_id in node_ids:
                duplicate_nodes += 1
                continue
            level = self._text(raw_node.get("level"), 80) or "variable"
            kind = self._text(raw_node.get("kind") or raw_node.get("type"), 80) or level
            name = self._text(raw_node.get("name") or node_id.rsplit("::", 1)[-1], 500)
            nodes.append(
                GraphNodeDTO(
                    id=node_id,
                    name=name,
                    kind=kind,
                    level=level,
                    scope=self._scope(node_id, level),
                    file=self._safe_relative_path(raw_node.get("file")),
                    line=self._positive_int(raw_node.get("line")),
                    end_line=self._positive_int(raw_node.get("end_line")),
                    lang=self._text(raw_node.get("lang"), 40) or None,
                )
            )
            node_ids.add(node_id)

        if len(raw_nodes) > self.max_nodes:
            warnings.append(f"node_limit_exceeded:{len(raw_nodes) - self.max_nodes}")
        if skipped_nodes:
            warnings.append(f"invalid_nodes_removed:{skipped_nodes}")
        if duplicate_nodes:
            warnings.append(f"duplicate_nodes_removed:{duplicate_nodes}")

        edges: list[GraphEdgeDTO] = []
        edge_keys: set[tuple[str, str, str, str]] = set()
        dangling_edges = 0
        self_loops = 0
        duplicate_edges = 0
        invalid_edges = 0
        in_degree: Counter[str] = Counter()
        out_degree: Counter[str] = Counter()

        for index, raw_edge in enumerate(raw_edges[: self.max_edges]):
            if not isinstance(raw_edge, dict):
                invalid_edges += 1
                continue
            source = self._endpoint(raw_edge.get("source"))
            target = self._endpoint(raw_edge.get("target"))
            if not source or not target:
                invalid_edges += 1
                continue
            if source == target:
                self_loops += 1
                continue
            if source not in node_ids or target not in node_ids:
                dangling_edges += 1
                continue
            relation = self._text(raw_edge.get("relation") or raw_edge.get("type"), 80) or "calls"
            dispatch = self._text(raw_edge.get("dispatch"), 80)
            key = (source, target, relation, dispatch)
            if key in edge_keys:
                duplicate_edges += 1
                continue
            edge_id = self._text(raw_edge.get("id"), 2200) or f"edge:{index}:{source}->{target}:{relation}"
            edges.append(
                GraphEdgeDTO(
                    id=edge_id,
                    source=source,
                    target=target,
                    relation=relation,
                    dispatch=dispatch or None,
                    dynamic=dispatch.lower() == "dynamic",
                )
            )
            edge_keys.add(key)
            out_degree[source] += 1
            in_degree[target] += 1

        if len(raw_edges) > self.max_edges:
            warnings.append(f"edge_limit_exceeded:{len(raw_edges) - self.max_edges}")
        for count, label in (
            (invalid_edges, "invalid_edges_removed"),
            (dangling_edges, "dangling_edges_removed"),
            (self_loops, "self_loops_removed"),
            (duplicate_edges, "duplicate_edges_removed"),
        ):
            if count:
                warnings.append(f"{label}:{count}")

        degrees: list[int] = []
        level_counts: Counter[str] = Counter()
        scope_counts: Counter[str] = Counter()
        normalized_nodes: list[GraphNodeDTO] = []
        for node in nodes:
            incoming = in_degree[node.id]
            outgoing = out_degree[node.id]
            degree = incoming + outgoing
            degrees.append(degree)
            level_counts[node.level] += 1
            scope_counts[node.scope] += 1
            normalized_nodes.append(
                node.model_copy(
                    update={"in_degree": incoming, "out_degree": outgoing, "degree": degree}
                )
            )
        relation_counts = Counter(edge.relation for edge in edges)
        truncated = len(raw_nodes) > self.max_nodes or len(raw_edges) > self.max_edges
        summary = GraphSummaryDTO(
            node_count=len(normalized_nodes),
            edge_count=len(edges),
            level_counts=dict(level_counts),
            relation_counts=dict(relation_counts),
            scope_counts=dict(scope_counts),
            max_degree=max(degrees, default=0),
            min_degree=min(degrees, default=0),
            truncated=truncated,
        )
        return DependencyGraphDTO(
            nodes=normalized_nodes,
            edges=edges,
            summary=summary,
            warnings=warnings,
        )

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        if value is None:
            return ""
        return str(value).replace("\x00", "").strip()[:limit]

    @classmethod
    def _endpoint(cls, value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("id")
        return cls._text(value, 1000)

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @classmethod
    def _safe_relative_path(cls, value: Any) -> str | None:
        text = cls._text(value, 1000).replace("\\", "/")
        if not text:
            return None
        windows_path = PureWindowsPath(text)
        posix_path = PurePosixPath(text)
        if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
            return None
        if any(part == ".." for part in posix_path.parts):
            return None
        normalized = posix_path.as_posix()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized or None

    @staticmethod
    def _scope(node_id: str, level: str) -> str:
        lowered_id = node_id.lower()
        lowered_level = level.lower()
        if lowered_level == "builtin" or lowered_id.startswith("<builtin>"):
            return "builtin"
        if lowered_level in {"stdlib", "stdlib_module"} or lowered_id.startswith("<stdlib>"):
            return "stdlib"
        if lowered_level in {"external", "external_module"} or lowered_id.startswith("<external>"):
            return "external"
        return "project"