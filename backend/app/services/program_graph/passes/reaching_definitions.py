"""基于 CFG 的跨语言到达定义分析。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque

from ..contracts import FunctionProgramGraph, ProgramGraphEdge

Definition = tuple[str, str]


class ReachingDefinitionsPass:
    """用通用工作列表算法生成变量级 ``reaching_def`` Overlay。"""

    def apply(self, graph: FunctionProgramGraph) -> FunctionProgramGraph:
        """输入函数 CFG，输出追加到达定义边的新函数图。"""
        cfg_edges = [edge for edge in graph.edges if edge.kind == "cfg"]
        predecessors: dict[str, set[str]] = defaultdict(set)
        successors: dict[str, set[str]] = defaultdict(set)
        for edge in cfg_edges:
            predecessors[edge.target].add(edge.source)
            successors[edge.source].add(edge.target)
        reachable = self._reachable(graph.entry_node_id, successors)
        definitions_by_variable: dict[str, set[Definition]] = defaultdict(set)
        generated: dict[str, set[Definition]] = defaultdict(set)
        for node in graph.nodes.values():
            if node.node_id not in reachable:
                continue
            for variable in node.definitions:
                definition = (node.node_id, variable)
                definitions_by_variable[variable].add(definition)
                generated[node.node_id].add(definition)
        killed: dict[str, set[Definition]] = defaultdict(set)
        for node_id, definitions in generated.items():
            for _, variable in definitions:
                killed[node_id].update(definitions_by_variable[variable] - definitions)

        incoming = {node_id: set() for node_id in reachable}
        outgoing = {node_id: set(generated[node_id]) for node_id in reachable}
        pending = deque(reachable)
        queued = set(reachable)
        while pending:
            node_id = pending.popleft()
            queued.discard(node_id)
            reachable_predecessors = predecessors[node_id].intersection(reachable)
            new_in = set().union(*(
                outgoing[item] for item in reachable_predecessors
            )) if reachable_predecessors else set()
            new_out = generated[node_id] | (new_in - killed[node_id])
            if new_in == incoming[node_id] and new_out == outgoing[node_id]:
                continue
            incoming[node_id] = new_in
            outgoing[node_id] = new_out
            for successor in successors[node_id].intersection(reachable):
                if successor in queued:
                    continue
                pending.append(successor)
                queued.add(successor)

        overlay: list[ProgramGraphEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for node in graph.nodes.values():
            if node.node_id not in reachable:
                continue
            for variable in node.uses:
                sources = sorted(
                    source for source, name in incoming[node.node_id]
                    if name == variable
                )
                certainty = "must" if len(sources) == 1 else "may"
                for source in sources:
                    key = (source, node.node_id, variable)
                    if key in seen:
                        continue
                    seen.add(key)
                    material = "\x1f".join(key)
                    overlay.append(ProgramGraphEdge(
                        edge_id="reaching-def:" + hashlib.sha256(
                            material.encode("utf-8")
                        ).hexdigest()[:24],
                        source=source,
                        target=node.node_id,
                        kind="reaching_def",
                        variable=variable,
                        certainty=certainty,
                    ))
        return graph.model_copy(update={"edges": cfg_edges + overlay})

    @staticmethod
    def _reachable(entry: str, successors: dict[str, set[str]]) -> set[str]:
        """返回从函数入口真实可达的 CFG 节点。"""
        result: set[str] = set()
        pending = [entry]
        while pending:
            node_id = pending.pop()
            if node_id in result:
                continue
            result.add(node_id)
            pending.extend(successors[node_id] - result)
        return result
