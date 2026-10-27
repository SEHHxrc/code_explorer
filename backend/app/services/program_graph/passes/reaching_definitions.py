"""基于 CFG 的跨语言到达定义分析。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque

from ..contracts import FunctionProgramGraph, ProgramGraphEdge
from backend.app.services.value_binding.identities import value_identity, variable_root
from backend.app.services.value_binding.objects import CollectionElementSlot, CollectionSummarySlot

Definition = tuple[str, str]


class ReachingDefinitionsPass:
    """用通用工作列表算法生成变量级 ``reaching_def`` Overlay。"""

    def apply(self, graph: FunctionProgramGraph) -> FunctionProgramGraph:
        """输入函数 CFG，输出追加到达定义边的新函数图。"""
        cfg_edges = [edge for edge in graph.edges if edge.kind == "cfg"]
        predecessors: dict[str, set[str]] = defaultdict(set)
        successors: dict[str, set[str]] = defaultdict(set)
        incoming_edges: dict[str, list[ProgramGraphEdge]] = defaultdict(list)
        for edge in cfg_edges:
            predecessors[edge.target].add(edge.source)
            successors[edge.source].add(edge.target)
            incoming_edges[edge.target].append(edge)
        reachable = self._reachable(graph.entry_node_id, successors)
        definitions_by_variable: dict[str, set[Definition]] = defaultdict(set)
        generated: dict[str, set[Definition]] = defaultdict(set)
        for node in graph.nodes.values():
            if node.node_id not in reachable:
                continue
            for variable in node.definitions:
                definition = (node.node_id, variable)
                definitions_by_variable[value_identity(node, variable, write=True)].add(definition)
                generated[node.node_id].add(definition)
        identities = {(node_id, name): value_identity(graph.nodes[node_id], name, write=True)
                      for node_id, definitions in generated.items() for _, name in definitions}
        # 每个已定义父访问路径只索引一次后代，避免每个写入节点重扫全部变量名。
        descendants: dict[str, set[Definition]] = defaultdict(set)
        for name, candidates in definitions_by_variable.items():
            for position, separator in enumerate(name):
                if separator in ".[":
                    parent = name[:position]
                    if parent in definitions_by_variable:
                        descendants[parent].update(candidates)
        killed: dict[str, set[Definition]] = defaultdict(set)
        for node_id, definitions in generated.items():
            if graph.nodes[node_id].certainty == "may":
                # 框架事件可能未发生：不能用可能写入杀死此前的真实定义。
                continue
            for _, variable in definitions:
                writes = [item for item in graph.nodes[node_id].value_transfers if item.output_variable == variable]
                if writes and all(item.weak_update for item in writes):
                    continue  # 某个元素的弱写不能杀旧值，也不应把同语句其他确定定义一并弱化。
                identity = identities[(node_id, variable)]
                killed[node_id].update(definitions_by_variable[identity] - definitions)
                killed[node_id].update(descendants[identity] - definitions)

        incoming = {node_id: set() for node_id in reachable}
        outgoing = {node_id: set(generated[node_id]) for node_id in reachable}
        pending = deque(reachable)
        queued = set(reachable)
        while pending:
            node_id = pending.popleft()
            queued.discard(node_id)
            reachable_predecessors = predecessors[node_id].intersection(reachable)
            new_in = set().union(*((incoming[edge.source] if edge.source_before_write else outgoing[edge.source])
                for edge in incoming_edges[node_id] if edge.source in reachable)) if reachable_predecessors else set()
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
                read_id = value_identity(node, variable, write=False)
                # 已知不同声明绝不串联；一端未知时保持 may，不把缺失绑定当作安全。
                def compatible(source: str, name: str) -> bool:
                    """已知不同声明不可连边；任一端未知只允许保守匹配，不证明同一实体。"""
                    source_id = identities[(source, name)]
                    expected = read_id[:len(read_id) - len(variable) + len(name)] if name != variable else read_id
                    if source_id.startswith("unresolved-storage:") or read_id.startswith("unresolved-storage:"):
                        return source_id == expected
                    return source_id == expected or source_id.startswith("lexical:") or read_id.startswith("lexical:")

                sources = [(source, name) for source, name in incoming[node.node_id]
                           if identities[(source, name)] == read_id or (name == variable and compatible(source, name))]
                summary = node.use_object_slots.get(variable)
                if isinstance(summary, CollectionSummarySlot):
                    # 未知键读取只合并同一集合分配的元素内容，不把键本身或相邻对象带入。
                    sources.extend((source, name) for source, name in incoming[node.node_id]
                        if isinstance(slot := graph.nodes[source].definition_object_slots.get(name), (CollectionElementSlot, CollectionSummarySlot))
                        and slot.allocation_id == summary.allocation_id and (source, name) not in sources)
                if read_id.startswith("object:"):
                    # 别名显示名不同，仍可由同一分配的父字段写入到达类型化子槽。
                    # 同节点已有精确类型定义时不补其合并父槽，避免多类型工厂兄弟值串联。
                    exact_nodes = {source for source, _ in sources}
                    parents = [(source, name) for source, name in incoming[node.node_id]
                               if source not in exact_nodes and read_id.startswith((
                                   identities[(source, name)] + ".", identities[(source, name)] + "["))]
                    if parents:
                        longest = max(len(identities[(source, name)]) for source, name in parents)
                        sources.extend((source, name) for source, name in parents
                                       if len(identities[(source, name)]) == longest)
                if not sources or (not read_id.startswith("lexical:") and any(
                    identities[(source, name)].startswith("lexical:") for source, name in sources
                )):
                    parents = [(source, name) for source, name in incoming[node.node_id]
                               if variable.startswith((name + ".", name + "[")) and compatible(source, name)]
                    if parents:
                        longest = max(len(name) for _, name in parents)
                        sources.extend((source, name) for source, name in parents if len(name) == longest)
                certainty = "must" if len(sources) == 1 and sources[0][1] == variable and not isinstance(summary, CollectionSummarySlot) else "may"
                for source, source_name in sorted(sources):
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
                        source_variable=source_name,
                        target_variable=variable,
                        source_value_id=identities[(source, source_name)],
                        target_value_id=read_id, read_value_id=read_id,
                        certainty="may" if graph.nodes[source].certainty == "may" or node.certainty == "may"
                            or any(transfer.weak_update and transfer.output_variable == source_name for transfer in graph.nodes[source].value_transfers)
                            or identities[(source, source_name)] != read_id
                            or "read:" + variable_root(variable) in node.binding_gaps
                            or "write:" + variable_root(source_name) in graph.nodes[source].binding_gaps else certainty,
                    ))
        # 调用方重算过公共 Def-Use 后，旧的原生来源标签不再有效；最终选择器会重新标记。
        return graph.model_copy(update={"edges": cfg_edges + overlay, "value_backend": None, "control_dependence": None})

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
