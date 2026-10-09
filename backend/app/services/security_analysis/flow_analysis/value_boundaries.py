"""语言无关的值边界编译与捕获入口 Overlay，不识别框架或生成安全事实。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from backend.app.services.program_graph.contracts import (
    FunctionProgramGraph,
    ProgramGraphArtifact,
    ProgramGraphNode,
    ProgramGraphLocation,
)
from backend.app.services.program_graph.passes import (
    ReachingDefinitionsPass,
    ValueFlowPass,
)

from ..ir import IRLocation, IRValueBoundary


@dataclass(frozen=True)
class ValueBoundaryTransition:
    """绑定到实际函数/操作节点的 may 值传递，保存原始适配器证据。"""

    boundary: IRValueBoundary
    source_method_id: str
    source_node_id: str
    target_method_id: str
    target_node_id: str


class ValueBoundaryIndex:
    """将规则无关边界绑定到公共图，并对需要的捕获槽位重算局部 Def-Use。

    输入函数分区图和静态适配器输出；返回独立 Overlay 视图及源函数索引。
    无边界时复用原图，不创建图、Source 或 CALLS。原始函数图不就地修改。
    """

    def __init__(
        self, graph: ProgramGraphArtifact, boundaries: list[IRValueBoundary]
    ) -> None:
        """绑定精确范围，过滤不可达写入，并只为实际捕获目标重算局部 Overlay。"""
        self.graph = graph
        self._by_source: dict[str, list[ValueBoundaryTransition]] = defaultdict(list)
        if not boundaries:
            return
        functions: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
        for function in graph.functions.values():
            functions[function.location.path].append(function)
        captures: dict[str, set[str]] = defaultdict(set)
        exports: dict[str, set[str]] = defaultdict(set)
        reachable: dict[str, set[str]] = {}
        for boundary in boundaries:
            source = self._function(
                functions.get(boundary.source_scope.path, []), boundary.source_scope
            )
            target = self._function(
                functions.get(boundary.target_scope.path, []), boundary.target_scope
            )
            if source is None or target is None:
                continue
            source_node = (
                source.nodes[source.exit_node_id]
                if boundary.source_at_exit
                else self._node(source, boundary.source_location)
            )
            target_node = (
                target.nodes[target.entry_node_id]
                if boundary.capture
                else self._node(target, boundary.target_location)
            )
            if source_node is None or target_node is None:
                continue
            if not boundary.source_at_exit and not set(boundary.source_names).issubset(
                set(source_node.uses) | set(source_node.definitions)
            ):
                continue
            if not boundary.capture and not set(boundary.target_names).issubset(
                target_node.definitions
            ):
                continue
            for function in (source, target):
                if function.method_id not in reachable:
                    successors: dict[str, set[str]] = defaultdict(set)
                    for edge in function.edges:
                        if edge.kind == "cfg":
                            successors[edge.source].add(edge.target)
                    reachable[function.method_id] = ReachingDefinitionsPass._reachable(
                        function.entry_node_id, successors
                    )
            if (
                source_node.node_id not in reachable[source.method_id]
                or target_node.node_id not in reachable[target.method_id]
            ):
                continue
            self._by_source[source.method_id].append(
                ValueBoundaryTransition(
                    boundary,
                    source.method_id,
                    source_node.node_id,
                    target.method_id,
                    target_node.node_id,
                )
            )
            if boundary.capture:
                captures[target.method_id].update(boundary.target_names)
            if boundary.source_at_exit:
                exports[source.method_id].update(boundary.source_names)
        if captures or exports:
            updated = dict(graph.functions)
            for method_id in captures.keys() | exports.keys():
                function = updated[method_id]
                entry = function.nodes[function.entry_node_id]
                nodes = dict(function.nodes)
                if method_id in captures:
                    nodes[entry.node_id] = entry.model_copy(
                        update={
                            "definitions": sorted(
                                set(entry.definitions) | captures[method_id]
                            )
                        }
                    )
                if method_id in exports:
                    exit_node = nodes[function.exit_node_id]
                    nodes[exit_node.node_id] = exit_node.model_copy(
                        update={
                            "uses": sorted(set(exit_node.uses) | exports[method_id])
                        }
                    )
                view = function.model_copy(update={"nodes": nodes})
                updated[method_id] = ValueFlowPass().apply(
                    ReachingDefinitionsPass().apply(view)
                )
            self.graph = graph.model_copy(update={"functions": updated})

    def for_source(self, method_id: str) -> tuple[ValueBoundaryTransition, ...]:
        """返回源函数的稳定边界列表，避免每个污点状态扫描项目全部边界。"""
        return tuple(self._by_source.get(method_id, ()))

    @staticmethod
    def _contains(scope: ProgramGraphLocation, location: IRLocation) -> bool:
        """按路径和完整行列范围匹配，不能只比较函数名或行号。"""
        return (
            scope.path == location.path
            and (scope.line, scope.column) <= (location.line, location.column)
            and (location.end_line, location.end_column)
            <= (scope.end_line, scope.end_column)
        )

    @classmethod
    def _function(
        cls, functions: list[FunctionProgramGraph], scope: IRLocation
    ) -> FunctionProgramGraph | None:
        """选择包含完整回调范围的最小函数；重载/同名不凭名字选目标。"""
        matches = [
            function
            for function in functions
            if cls._contains(function.location, scope)
        ]
        return (
            min(
                matches,
                key=lambda item: (
                    item.location.end_line - item.location.line,
                    item.location.end_column - item.location.column,
                ),
            )
            if matches
            else None
        )

    @classmethod
    def _node(
        cls, function: FunctionProgramGraph, location: IRLocation
    ) -> ProgramGraphNode | None:
        """定位真实操作，排除生成的函数入口/出口。"""
        matches = [
            (
                (
                    node.location.end_line - node.location.line,
                    node.location.end_column - node.location.column,
                ),
                node,
            )
            for node in function.nodes.values()
            if node.location is not None
            and node.kind not in {"method_entry", "method_exit"}
            and cls._contains(node.location, location)
        ]
        return min(matches, key=lambda item: item[0])[1] if matches else None
