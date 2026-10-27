"""语言无关的支配、后支配和控制依赖；只消费最终 CFG，不重做语法或值流分析。"""

import hashlib

import networkx as nx

from ..conditions import ControlDependenceSummary
from ..contracts import FunctionProgramGraph, ProgramGraphEdge


class ControlDependencePass:
    """在反向 CFG 上求后支配，再沿后支配树为分支边建立控制依赖。"""

    def apply(self, function: FunctionProgramGraph) -> FunctionProgramGraph:
        """返回新图；保留其他 Overlay，并显式披露不可达、不通向出口和异常模型边界。"""
        cfg = nx.DiGraph()
        cfg.add_nodes_from(function.nodes)
        edges = [edge for edge in function.edges if edge.kind == "cfg"]
        cfg.add_edges_from((edge.source, edge.target) for edge in edges)
        reachable = {function.entry_node_id, *nx.descendants(cfg, function.entry_node_id)}
        view = cfg.subgraph(reachable).copy()
        dominators = nx.immediate_dominators(view, function.entry_node_id)
        exit_reaching = ({function.exit_node_id, *nx.ancestors(view, function.exit_node_id)}
                         if function.exit_node_id in view else set())
        non_exit = reachable - exit_reaching
        # 每个不能到达真实出口的节点都有虚拟逃逸边，而不是随意选一个 SCC 代表。
        # 这会弱化该区域的后支配关系，避免把“不终止”误当成必经真实出口。
        virtual = object()
        view.add_node(virtual)
        for node_id in non_exit | ({function.exit_node_id} & reachable):
            view.add_edge(node_id, virtual)
        post = nx.immediate_dominators(view.reverse(copy=False), virtual)
        dependencies: list[ProgramGraphEdge] = []
        keys: set[tuple[str, str, str | None]] = set()
        for edge in edges:
            if edge.source not in reachable or edge.target not in reachable:
                continue
            runner = edge.target
            stop = post.get(edge.source)
            while runner != stop and runner is not virtual:
                key = (edge.source, runner, edge.branch)
                if key not in keys:
                    keys.add(key)
                    material = "\x1f".join((function.method_id, edge.source, runner, edge.branch or ""))
                    dependencies.append(ProgramGraphEdge(edge_id="control:" + hashlib.sha256(material.encode()).hexdigest()[:24],
                        kind="control_dependence", source=edge.source, target=runner, branch=edge.branch,
                        certainty="may", provenance="inferred"))
                runner = post[runner]
        summary = ControlDependenceSummary(immediate_dominators=dominators,
            immediate_postdominators={key: "" if parent is virtual else parent for key, parent in post.items() if key is not virtual},
            unreachable_nodes=sorted(set(function.nodes) - reachable), non_exit_reaching_nodes=sorted(non_exit),
            limitations=["控制依赖相对于已构建 CFG；隐式异常、finally 重放与终止性不作完整证明。"] +
                (["不通向真实出口的区域以逐节点虚拟逃逸边保守增强。"] if non_exit else []))
        return function.model_copy(update={"edges": [edge for edge in function.edges if edge.kind != "control_dependence"] + dependencies,
                                           "control_dependence": summary})
