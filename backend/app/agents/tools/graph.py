"""基于共享邻接索引的依赖图工具。"""

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.tools.arguments import DependencyArguments
from backend.app.agents.tools.base import AgentTool, ToolContext


class DependencyNeighborsTool(AgentTool):
    name = "get_dependency_neighbors"
    description = "Return incoming and/or outgoing dependency-graph neighbors for an exact node id."
    arguments_model = DependencyArguments

    async def execute(self, context: ToolContext, arguments: DependencyArguments) -> ToolResult:
        index = context.evidence_index
        if arguments.node_id not in index.nodes:
            raise ValueError("Dependency node was not found; use search_symbols first")
        candidates = []
        if arguments.direction in {"both", "outgoing"}:
            candidates.extend(("outgoing", edge) for edge in index.outgoing.get(arguments.node_id, []))
        if arguments.direction in {"both", "incoming"}:
            candidates.extend(("incoming", edge) for edge in index.incoming.get(arguments.node_id, []))
        rows = []
        evidence = []
        for direction, edge in candidates:
            source = index._endpoint(edge.get("source"))
            target = index._endpoint(edge.get("target"))
            neighbor = target if direction == "outgoing" else source
            node = index.nodes.get(neighbor, {})
            relation = edge.get("relation") or edge.get("type")
            rows.append({
                "direction": direction,
                "relation": relation,
                "node_id": neighbor,
                "name": node.get("name"),
                "kind": node.get("kind") or node.get("type"),
                "file": node.get("file"),
                "line": node.get("line"),
            })
            if node.get("file"):
                evidence.append(AgentEvidence(
                    path=node["file"], line=node.get("line"), symbol=node.get("name"),
                    detail=f"{direction} {relation or 'dependency'}",
                ))
            if len(rows) >= arguments.limit:
                return ToolResult(content=rows, evidence=evidence, truncated=True)
        return ToolResult(content=rows, evidence=evidence)