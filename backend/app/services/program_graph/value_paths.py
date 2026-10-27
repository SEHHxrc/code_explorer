"""在已有值流图上查询有界路径变体；不解析语言、不推断防护或新增传播边。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from backend.app.services.value_binding.identities import value_identity
from .contracts import FunctionProgramGraph, ProgramGraphEdge
from .passes.reaching_definitions import ReachingDefinitionsPass


@dataclass(frozen=True)
class ValuePaths:
    """已保留的不同边序列及查询边界；truncated 不表示已证明还有可行路径。"""

    paths: tuple[tuple[ProgramGraphEdge, ...], ...] = ()
    truncated: bool = False
    explored_states: int = 0


class ValuePathSearch:
    """共享单路径/多路径查询；汇合点按路线保留身份，不用全局 visited 丢弃较长分支。"""

    def __init__(self, function: FunctionProgramGraph) -> None:
        """输入最终只读值流 Overlay，缓存邻接表供本次函数内的多次查询复用。"""
        self.function = function
        self.adjacency: dict[str, list[ProgramGraphEdge]] = defaultdict(list)
        self.reverse: dict[str, set[str]] = defaultdict(set)
        self._toward: dict[str, set[str]] = {}
        values = [edge for edge in function.edges if edge.kind == "value_flow"]
        for edge in values or [edge for edge in function.edges if edge.kind == "reaching_def"]:
            self.adjacency[edge.source].append(edge)
            self.reverse[edge.target].add(edge.source)
        for edges in self.adjacency.values():
            edges.sort(key=lambda edge: (function.nodes[edge.target].location.line if function.nodes[edge.target].location else 0,
                edge.target, edge.source_variable or edge.variable or "", edge.target_variable or edge.variable or "", edge.edge_id))
        successors: dict[str, set[str]] = defaultdict(set)
        for edge in function.edges:
            if edge.kind == "cfg":
                successors[edge.source].add(edge.target)
        self.reachable = ReachingDefinitionsPass._reachable(function.entry_node_id, successors)

    def search(self, *, source_node_id: str, source_names: tuple[str, ...], sink_node_id: str,
               sink_names: set[str], reads_input: bool = True, limit: int = 8, max_states: int = 2048,
               probe_extra: bool = True) -> ValuePaths:
        """返回不重复值状态的有界边序列；达到预算/循环边界保留截断，不宣称穷尽 CFG。"""
        if min(limit, max_states) < 1:
            raise ValueError("值路径预算必须为正数")
        function = self.function
        if source_node_id not in self.reachable or sink_node_id not in self.reachable:
            return ValuePaths()
        if sink_node_id not in self._toward:
            toward, stack = {sink_node_id}, [sink_node_id]
            while stack:
                for parent in self.reverse.get(stack.pop(), ()):
                    if parent not in toward:
                        toward.add(parent)
                        stack.append(parent)
            self._toward[sink_node_id] = toward
        toward = self._toward[sink_node_id]
        initial = tuple(dict.fromkeys((source_node_id, value_identity(function.nodes[source_node_id], name,
            write=name in function.nodes[source_node_id].definitions))
            for name in source_names or tuple(function.nodes[source_node_id].definitions)))
        direct = source_node_id == sink_node_id and any(
            value_identity(function.nodes[source_node_id], name, write=name in function.nodes[source_node_id].definitions)
            == value_identity(function.nodes[sink_node_id], name, write=not reads_input)
            for name in set(source_names) & sink_names)
        paths: list[tuple[ProgramGraphEdge, ...]] = [()] if direct else []
        if direct and not probe_extra:
            return ValuePaths(((),))
        keys = {()} if direct else set()
        pending = deque((state, (), frozenset((state,))) for state in initial)
        explored, truncated = 0, False
        while pending:
            if explored >= max_states:
                truncated = True
                break
            (node_id, name), prefix, visited = pending.popleft()
            explored += 1
            for edge in self.adjacency.get(node_id, []):
                if edge.target not in toward:
                    continue
                if (edge.source_value_id or value_identity(function.nodes[edge.source],
                    edge.source_variable or edge.variable or name, write=True)) != name:
                    continue
                target_name = edge.target_variable or edge.variable or name
                state = (edge.target, edge.target_value_id or value_identity(function.nodes[edge.target],
                    target_name, write=target_name in function.nodes[edge.target].definitions))
                if edge.target == sink_node_id and ((edge.read_variable or target_name) if reads_input else target_name) not in sink_names:
                    continue
                if state in visited:
                    truncated = True  # 不展开循环值路径；与已实现的 CFG 条件核验分开。
                    continue
                path = (*prefix, edge)
                if edge.target == sink_node_id:
                    key = tuple(item.edge_id for item in path)
                    if key not in keys:
                        if len(paths) >= limit:
                            return ValuePaths(tuple(paths), True, explored)
                        keys.add(key)
                        paths.append(path)
                        if not probe_extra and len(paths) >= limit:
                            return ValuePaths(tuple(paths), bool(pending), explored)
                else:
                    pending.append((state, path, visited | {state}))
                    if len(pending) + explored > max_states:
                        return ValuePaths(tuple(paths), True, explored)
        return ValuePaths(tuple(paths), truncated, explored)
