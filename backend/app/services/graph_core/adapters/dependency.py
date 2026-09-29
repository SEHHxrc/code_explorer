"""现有 NetworkX node-link 依赖图到公共图接口的只读适配器。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from ..contracts import GraphCapabilities, GraphEdgeRef, GraphKind, GraphNodeRef


class DependencyGraphView:
    """在不改变依赖图持久化格式的前提下提供统一只读视图。"""

    def __init__(
        self,
        graph: dict[str, Any],
        *,
        schema_version: str = "node-link/1",
    ) -> None:
        """包装 NetworkX node-link 字典并保留原始数据所有权。"""
        self.graph = graph
        self._schema_version = schema_version

    @property
    def schema_version(self) -> str:
        """返回适配视图使用的依赖图版本标识。"""
        return self._schema_version

    @property
    def graph_kind(self) -> GraphKind:
        """返回宏观依赖图类别。"""
        return "dependency_graph"

    @property
    def capabilities(self) -> GraphCapabilities:
        """返回当前依赖图确定具备的能力。"""
        return GraphCapabilities(
            hierarchical=True,
            cross_file=True,
            interprocedural=True,
            multigraph=True,
        )

    def iter_nodes(self) -> Iterable[GraphNodeRef]:
        """按节点身份稳定迭代 node-link 节点。"""
        nodes = [
            GraphNodeRef(
                node_id=str(item.get("id") or ""),
                kind=str(item.get("kind") or item.get("type") or "unknown"),
            )
            for item in self.graph.get("nodes", []) or []
            if isinstance(item, dict)
        ]
        return iter(sorted(nodes, key=lambda item: (item.node_id, item.kind)))

    def iter_edges(self) -> Iterable[GraphEdgeRef]:
        """按边身份稳定迭代 node-link 边，并为旧边生成视图内身份。"""
        edges: list[GraphEdgeRef] = []
        raw_edges = self.graph.get("links", self.graph.get("edges", [])) or []
        for index, item in enumerate(raw_edges):
            if not isinstance(item, dict):
                continue
            source = self._endpoint(item.get("source"))
            target = self._endpoint(item.get("target"))
            kind = str(item.get("relation") or item.get("type") or "unknown")
            edge_id = str(item.get("id") or "") or self._legacy_edge_id(
                source,
                target,
                kind,
                str(item.get("key") or index),
            )
            edges.append(GraphEdgeRef(
                edge_id=edge_id,
                source=source,
                target=target,
                kind=kind,
            ))
        return iter(sorted(edges, key=lambda item: (
            item.edge_id,
            item.source,
            item.target,
        )))

    @staticmethod
    def _endpoint(value: Any) -> str:
        """读取 NetworkX node-link 的字符串或对象端点。"""
        if isinstance(value, dict):
            value = value.get("id")
        return "" if value is None else str(value)

    @staticmethod
    def _legacy_edge_id(source: str, target: str, kind: str, key: str) -> str:
        """为没有正式 edge_id 的旧结构边生成只用于适配视图的稳定身份。"""
        material = "\x1f".join((source, target, kind, key))
        return "dependency-view-edge:" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:24]

