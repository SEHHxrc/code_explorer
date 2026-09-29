"""按函数分区的 ProgramGraphArtifact 到公共图接口的只读适配器。"""

from __future__ import annotations

from collections.abc import Iterable

from backend.app.services.program_graph import ProgramGraphArtifact

from ..contracts import GraphCapabilities, GraphEdgeRef, GraphKind, GraphNodeRef


class ProgramGraphView:
    """将全部函数分区投影为只用于通用工具的扁平视图。"""

    def __init__(self, artifact: ProgramGraphArtifact) -> None:
        """保存类型化 ProgramGraph 产物，不复制函数图。"""
        self.artifact = artifact

    @property
    def schema_version(self) -> str:
        """返回 ProgramGraph 协议版本。"""
        return self.artifact.schema_version

    @property
    def graph_kind(self) -> GraphKind:
        """返回微观程序图类别。"""
        return "program_graph"

    @property
    def capabilities(self) -> GraphCapabilities:
        """返回当前 ProgramGraph 确定具备的能力。"""
        return GraphCapabilities(
            control_flow="cfg" in self.artifact.overlays,
            data_flow=bool(
                {"reaching_definitions", "value_flow"}.intersection(
                    self.artifact.overlays,
                )
            ),
            function_partitioned=True,
        )

    def iter_nodes(self) -> Iterable[GraphNodeRef]:
        """跨函数分区按节点身份稳定迭代操作节点。"""
        nodes = [
            GraphNodeRef(node_id=node.node_id, kind=node.kind)
            for function in self.artifact.functions.values()
            for node in function.nodes.values()
        ]
        return iter(sorted(nodes, key=lambda item: (item.node_id, item.kind)))

    def iter_edges(self) -> Iterable[GraphEdgeRef]:
        """跨函数分区按边身份稳定迭代 CFG 和 Overlay 边。"""
        edges = [
            GraphEdgeRef(
                edge_id=edge.edge_id,
                source=edge.source,
                target=edge.target,
                kind=edge.kind,
            )
            for function in self.artifact.functions.values()
            for edge in function.edges
        ]
        return iter(sorted(edges, key=lambda item: (
            item.edge_id,
            item.source,
            item.target,
        )))

