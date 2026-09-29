"""不依赖 NetworkX 或 Pydantic 图内部结构的公共校验。"""

from __future__ import annotations

from .contracts import GraphValidationIssue
from .protocols import GraphArtifactView


class GraphViewValidator:
    """通过 GraphArtifactView 校验身份唯一性和边端点完整性。"""

    def validate(self, graph: GraphArtifactView) -> list[GraphValidationIssue]:
        """返回全部结构问题；不修改原图，也不抛弃不完整证据。"""
        issues: list[GraphValidationIssue] = []
        node_ids: set[str] = set()
        for node in graph.iter_nodes():
            if not node.node_id:
                issues.append(GraphValidationIssue(
                    code="empty_identity",
                    message="图节点缺少稳定身份。",
                ))
                continue
            if node.node_id in node_ids:
                issues.append(GraphValidationIssue(
                    code="duplicate_node",
                    message=f"节点身份重复：{node.node_id}",
                    node_id=node.node_id,
                ))
            node_ids.add(node.node_id)

        edge_ids: set[str] = set()
        for edge in graph.iter_edges():
            if not edge.edge_id:
                issues.append(GraphValidationIssue(
                    code="empty_identity",
                    message="图边缺少稳定身份。",
                ))
            elif edge.edge_id in edge_ids:
                issues.append(GraphValidationIssue(
                    code="duplicate_edge",
                    message=f"边身份重复：{edge.edge_id}",
                    edge_id=edge.edge_id,
                ))
            edge_ids.add(edge.edge_id)
            missing = [
                endpoint
                for endpoint in (edge.source, edge.target)
                if not endpoint or endpoint not in node_ids
            ]
            if missing:
                issues.append(GraphValidationIssue(
                    code="dangling_edge",
                    message=(
                        f"边 {edge.edge_id or '<empty>'} 包含不存在的端点："
                        + ", ".join(missing)
                    ),
                    edge_id=edge.edge_id or None,
                ))
        return issues

