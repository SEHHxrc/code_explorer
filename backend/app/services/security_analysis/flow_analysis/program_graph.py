"""基于公共 ProgramGraph 和已解析调用边的跨语言安全数据流。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Any

from backend.app.services.program_graph import (
    FunctionProgramGraph,
    ProgramCallSite,
    ProgramGraphArtifact,
    ProgramGraphEdge,
    ProgramGraphLocation,
    ProgramGraphNode,
)
from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact
from backend.app.services.program_graph.passes import ReachingDefinitionsPass

from ..contracts import (
    DataFlowEvidence,
    EvidenceLocation,
    FlowStepEvidence,
    StaticSecurityFact,
)
from ..registry import SemanticsRegistry
from .call_graph import CallTransitionIndex, ProgramArgumentBinder
from .value_boundaries import ValueBoundaryIndex
from .argument_slots import prepare_argument_slots
from .call_effects import CallOutputEffects
from .call_sources import prepare_call_sources
from ..ir import IRValueBoundary


@dataclass
class _ReturnContinuation:
    """被调用函数返回后恢复到调用者结果变量所需的上下文。"""

    caller: FunctionProgramGraph
    call_node_id: str
    callsite_id: str
    result_names: tuple[str, ...]
    certainty: str
    transition_id: str
    uncertain: bool = False
    reverse_boundary: bool = False
    result_slots: tuple[str, ...] = ()


@dataclass
class _FlowState:
    """跨过程搜索中位于一个函数入口或局部定义点的污点状态。"""

    function: FunctionProgramGraph
    node_id: str
    names: tuple[str, ...]
    steps: list[FlowStepEvidence]
    transition_ids: tuple[str, ...] = ()
    depth: int = 0
    uncertain: bool = False
    limitations: tuple[str, ...] = ()
    continuations: tuple[_ReturnContinuation, ...] = ()
    boundary_ids: tuple[str, ...] = ()


_ValueState = tuple[str, str]


class ProgramGraphSecurityFlowAnalyzer:
    """把公共 CFG/DDG Overlay 与 Source/Sink 事实连接为安全数据流。"""

    def __init__(
        self,
        *,
        max_call_depth: int = 4,
        semantics: SemanticsRegistry | None = None,
    ) -> None:
        """设置已解析调用参数传播的最大深度，避免递归和大调用图失控。"""
        self.max_call_depth = max(1, max_call_depth)
        self.argument_binder = ProgramArgumentBinder(semantics)

    def analyze(
        self,
        program_graph: ProgramGraphArtifact,
        *,
        sources: list[StaticSecurityFact],
        sinks: list[StaticSecurityFact],
        sanitizers: list[StaticSecurityFact],
        dependency_graph: dict[str, Any] | None = None,
        semantic_index: SemanticIndex | SemanticIndexArtifact | dict[str, Any] | None = None,
        value_boundaries: list[IRValueBoundary] | None = None,
    ) -> list[DataFlowEvidence]:
        """返回函数内及有界实参到形参传播能够建立的 Source-to-Sink 路径。"""
        semantic_view = SemanticIndex.load(semantic_index) if semantic_index is not None else None
        transitions = CallTransitionIndex.from_semantic_index(semantic_view) if semantic_view is not None and semantic_view.artifact.callsites else CallTransitionIndex.from_dependency_graph(dependency_graph or {})
        program_graph = prepare_call_sources(program_graph, sources)
        program_graph = prepare_argument_slots(program_graph, transitions, self.argument_binder)
        program_graph = CallOutputEffects.prepare(program_graph, sources)
        boundary_index = ValueBoundaryIndex(program_graph, value_boundaries or [])
        program_graph = boundary_index.graph
        functions_by_symbol: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
        for function in program_graph.functions.values():
            functions_by_symbol[function.symbol_id].append(function)
        sinks_by_symbol: dict[str, list[StaticSecurityFact]] = defaultdict(list)
        for sink in sinks:
            sinks_by_symbol[sink.symbol].append(sink)
        sanitizers_by_symbol: dict[str, list[StaticSecurityFact]] = defaultdict(list)
        for sanitizer in sanitizers:
            sanitizers_by_symbol[sanitizer.symbol].append(sanitizer)

        flows: list[DataFlowEvidence] = []
        seen: set[tuple[str, str, str]] = set()
        for source in sources:
            for function in functions_by_symbol.get(source.symbol, []):
                if not self._contains(function, source.location):
                    continue
                call_index = self._call_index(function)
                source_seed = self._source_seed(function, source, call_index)
                if source_seed is None:
                    continue
                sanitizer_nodes = self._sanitizer_nodes(
                    function,
                    sanitizers_by_symbol.get(source.symbol, []),
                    call_index,
                )
                for sink in sinks_by_symbol.get(source.symbol, []):
                    if not self._contains(function, sink.location):
                        continue
                    sink_target = self._sink_target(function, sink, call_index)
                    if sink_target is None:
                        continue
                    sink_node_id, sink_names = sink_target
                    path = self._path(
                        function,
                        source_node_id=source_seed[0],
                        source_names=source_seed[1],
                        sink_node_id=sink_node_id,
                        sink_names=sink_names,
                        blocked_nodes=sanitizer_nodes,
                    )
                    if path is None:
                        continue
                    identity = (source.fact_id, sink.fact_id, function.method_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    flows.append(self._flow(
                        function,
                        source,
                        sink,
                        source_names=source_seed[1],
                        sink_names=sink_names,
                        path=path,
                    ))
        if dependency_graph is not None or semantic_view is not None or value_boundaries:
            flows.extend(self._interprocedural_flows(
                dependency_graph=dependency_graph,
                semantic_index=semantic_view,
                sources=sources,
                sinks_by_symbol=sinks_by_symbol,
                sanitizers_by_symbol=sanitizers_by_symbol,
                functions_by_symbol=functions_by_symbol,
                excluded_pairs={
                    (flow.source_fact_id, flow.sink_fact_id)
                    for flow in flows
                },
                boundary_index=boundary_index,
            ))
        return sorted(flows, key=lambda item: item.flow_id)

    def _interprocedural_flows(
        self,
        *,
        dependency_graph: dict[str, Any] | None,
        semantic_index: SemanticIndex | None,
        sources: list[StaticSecurityFact],
        sinks_by_symbol: dict[str, list[StaticSecurityFact]],
        sanitizers_by_symbol: dict[str, list[StaticSecurityFact]],
        functions_by_symbol: dict[str, list[FunctionProgramGraph]],
        excluded_pairs: set[tuple[str, str]],
        boundary_index: ValueBoundaryIndex,
    ) -> list[DataFlowEvidence]:
        """沿确定调用点把实参绑定到目标形参，搜索有限深度的跨函数路径。"""
        transitions = (
            CallTransitionIndex.from_semantic_index(semantic_index)
            if semantic_index is not None and semantic_index.artifact.callsites
            else CallTransitionIndex.from_dependency_graph(dependency_graph or {})
        )
        results: list[DataFlowEvidence] = []
        completed_pairs = set(excluded_pairs)
        for source in sources:
            initial_states: list[_FlowState] = []
            for function in functions_by_symbol.get(source.symbol, []):
                if not self._contains(function, source.location):
                    continue
                seed = self._source_seed(function, source, self._call_index(function))
                if seed is None:
                    continue
                initial_states.append(_FlowState(
                    function=function,
                    node_id=seed[0],
                    names=seed[1],
                    steps=[self._fact_step(source, "source", seed[1])],
                    limitations=tuple(function.limitations),
                ))
            queue = deque(sorted(
                initial_states,
                key=lambda item: (item.function.method_id, item.node_id, item.names),
            ))
            visited: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
            while queue:
                state = queue.popleft()
                visit_key = (
                    state.function.method_id,
                    state.node_id,
                    state.names,
                    tuple(item.callsite_id for item in state.continuations),
                )
                if visit_key in visited:
                    continue
                visited.add(visit_key)
                call_index = self._call_index(state.function)
                blocked_nodes = self._sanitizer_nodes(
                    state.function,
                    sanitizers_by_symbol.get(state.function.symbol_id, []),
                    call_index,
                )
                for sink in sinks_by_symbol.get(state.function.symbol_id, []):
                    pair = (source.fact_id, sink.fact_id)
                    if pair in completed_pairs or not self._contains(state.function, sink.location):
                        continue
                    target = self._sink_target(state.function, sink, call_index)
                    if target is None:
                        continue
                    sink_node_id, sink_names = target
                    local_path = self._path(
                        state.function,
                        source_node_id=state.node_id,
                        source_names=state.names,
                        sink_node_id=sink_node_id,
                        sink_names=sink_names,
                        blocked_nodes=blocked_nodes,
                    )
                    if local_path is None:
                        continue
                    results.append(self._interprocedural_flow(
                        source,
                        sink,
                        state,
                        sink_names=sink_names,
                        final_path=local_path,
                    ))
                    completed_pairs.add(pair)
                queue.extend(self._return_states(
                    state,
                    blocked_nodes=blocked_nodes,
                    semantic_index=semantic_index,
                    transitions=transitions,
                    functions_by_symbol=functions_by_symbol,
                ))
                if state.depth >= self.max_call_depth:
                    continue
                queue.extend(self._boundary_states(state, boundary_index, blocked_nodes))
                for transition in transitions.for_source(state.function.symbol_id):
                    hit = call_index.get(transition.callsite_id)
                    if hit is None:
                        continue
                    call_node, call = hit
                    target_functions = functions_by_symbol.get(transition.target, [])
                    overloaded = len(target_functions) > 1
                    for target_function in target_functions:
                        for parameter, argument_names in self.argument_binder.bind(
                            call,
                            target_function,
                        ):
                            local_path = self._path(
                                state.function,
                                source_node_id=state.node_id,
                                source_names=state.names,
                                sink_node_id=call_node.node_id,
                                sink_names=set(argument_names),
                                blocked_nodes=blocked_nodes,
                            )
                            if local_path is None:
                                continue
                            uncertain = (
                                state.uncertain
                                or transition.certainty != "must"
                                or transition.confidence != "high"
                                or transition.truncated
                                or overloaded
                                or any(pattern.kind != "name" for pattern in target_function.parameter_patterns)
                                or any(edge.certainty == "may" for edge in local_path)
                            )
                            steps = list(state.steps)
                            steps.extend(self._assignment_steps(
                                state.function,
                                local_path,
                            ))
                            steps.append(self._call_step(
                                state.function,
                                target_function,
                                call,
                                input_names=argument_names,
                                parameter=parameter,
                                certainty="may" if uncertain else "must",
                            ))
                            limitations = tuple(dict.fromkeys((
                                *state.limitations,
                                *target_function.limitations,
                                *(
                                    ("调用目标存在多态、低置信度或截断，实参传播按 may 处理。",)
                                    if uncertain else ()
                                ),
                            )))
                            result_slot = (
                                semantic_index.call_result(call.callsite_id)
                                if semantic_index is not None else None
                            )
                            result_names = tuple(
                                dict.fromkeys(
                                    result_slot.value_names
                                    if result_slot is not None
                                    else call_node.definitions
                                )
                            )
                            continuations = state.continuations + (_ReturnContinuation(
                                caller=state.function,
                                call_node_id=call_node.node_id,
                                callsite_id=call.callsite_id,
                                result_names=result_names,
                                certainty=(
                                    result_slot.certainty
                                    if result_slot is not None else "may"
                                ),
                                transition_id=transition.edge_id,
                                uncertain=uncertain,
                                result_slots=tuple(call.result_targets),
                            ),)
                            queue.append(_FlowState(
                                function=target_function,
                                node_id=target_function.entry_node_id,
                                names=(parameter,),
                                steps=steps,
                                transition_ids=state.transition_ids + (transition.edge_id,),
                                depth=state.depth + 1,
                                uncertain=uncertain,
                                limitations=limitations,
                                continuations=continuations,
                                boundary_ids=state.boundary_ids,
                            ))
        return results

    def _boundary_states(
        self, state: _FlowState, index: ValueBoundaryIndex, blocked_nodes: set[str],
    ) -> list[_FlowState]:
        """复用局部值流验证写入，再经 may 边界进入读取侧；最多共用四层预算。"""
        result: list[_FlowState] = []
        for transition in index.for_source(state.function.method_id):
            boundary = transition.boundary
            path = self._path(
                state.function, source_node_id=state.node_id, source_names=state.names,
                sink_node_id=transition.source_node_id, sink_names=set(boundary.source_names),
                blocked_nodes=blocked_nodes,
            )
            if path is None:
                continue
            target = index.graph.functions[transition.target_method_id]
            writer = state.function.nodes[transition.source_node_id]
            reader = target.nodes[transition.target_node_id]
            steps = [*state.steps, *self._assignment_steps(state.function, path)]
            for suffix, function, location, expression in (
                ("write", state.function, boundary.source_location, f"{boundary.kind}: {', '.join(boundary.source_names)}" if boundary.source_at_exit else writer.code),
                ("read", target, boundary.target_location, f"{boundary.kind}: {', '.join(boundary.target_names)}" if boundary.capture else reader.code),
            ):
                steps.append(FlowStepEvidence(
                    step_id="flowstep:" + self._digest(boundary.boundary_id + suffix),
                    order=0, kind="assignment", symbol=function.symbol_id,
                    location=EvidenceLocation(**location.__dict__), expression=expression,
                    input_names=list(boundary.source_names if suffix == "write" else boundary.target_names),
                    output_names=list(boundary.target_names), provenance="inferred", certainty="may",
                    importance="essential",
                ))
            result.append(_FlowState(
                function=target, node_id=transition.target_node_id, names=boundary.target_names,
                steps=steps, transition_ids=state.transition_ids, depth=state.depth + 1,
                uncertain=True, boundary_ids=state.boundary_ids + (boundary.boundary_id,),
                limitations=tuple(dict.fromkeys((*state.limitations, *target.limitations, *boundary.limitations))),
            ))
        return result

    def _return_states(
        self,
        state: _FlowState,
        *,
        blocked_nodes: set[str],
        semantic_index: SemanticIndex | None,
        transitions: CallTransitionIndex,
        functions_by_symbol: dict[str, list[FunctionProgramGraph]],
    ) -> list[_FlowState]:
        """把可达 return 值映射回最近调用点的结果变量。"""
        continuations = list(state.continuations[-1:])
        if not continuations and state.depth < self.max_call_depth:
            continuations.extend(self._caller_continuations(
                state.function,
                transitions=transitions,
                functions_by_symbol=functions_by_symbol,
                semantic_index=semantic_index,
            ))
        continuations = [item for item in continuations if item.result_names]
        if not continuations:
            return []
        return_items: list[tuple[ProgramGraphNode, tuple[str, ...], str]] = []
        if semantic_index is not None:
            for fact in semantic_index.returns_for(state.function.method_id):
                node = state.function.nodes.get(fact.node_id)
                if node is not None and fact.value_names:
                    return_items.append((node, tuple(fact.value_names), fact.certainty))
        if not return_items:
            return_items = [
                (node, tuple(node.uses), "may")
                for node in state.function.nodes.values()
                if node.kind == "return" and node.uses
            ]
        result: list[_FlowState] = []
        for base_continuation in continuations:
            for return_node, value_names, return_certainty in return_items:
                values: list[tuple[int | None, tuple[str, ...]]] = [(None, value_names)]
                if return_node.return_values and base_continuation.result_slots:
                    values = [(index, tuple(names)) for index, names in enumerate(return_node.return_values) if names and index < len(base_continuation.result_slots)]
                for position, names in values:
                    continuation = replace(base_continuation, result_names=(base_continuation.result_slots[position],)) if position is not None else base_continuation
                    result.extend(self._return_value_states(state, continuation, return_node, names, return_certainty, blocked_nodes))
        return result

    def _return_value_states(self, state: _FlowState, continuation: _ReturnContinuation, return_node: ProgramGraphNode, value_names: tuple[str, ...], return_certainty: str, blocked_nodes: set[str]) -> list[_FlowState]:
        """将一个已选定返回分量映射到对应接收槽位，复用相同局部路径验证。"""
        result: list[_FlowState] = []
        if value_names:
                path = self._path(
                    state.function,
                    source_node_id=state.node_id,
                    source_names=state.names,
                    sink_node_id=return_node.node_id,
                    sink_names=set(value_names),
                    blocked_nodes=blocked_nodes,
                )
                if path is None:
                    return []
                uncertain = (
                    state.uncertain
                    or continuation.uncertain
                    or continuation.certainty != "must"
                    or return_certainty != "must"
                    or any(edge.certainty == "may" for edge in path)
                )
                steps = list(state.steps)
                steps.extend(self._assignment_steps(state.function, path))
                steps.append(self._return_step(
                    state.function,
                    continuation,
                    return_node,
                    input_names=value_names,
                    certainty="may" if uncertain else "must",
                ))
                transition_ids = state.transition_ids
                if continuation.transition_id not in transition_ids:
                    transition_ids = transition_ids + (continuation.transition_id,)
                result.append(_FlowState(
                    function=continuation.caller,
                    node_id=continuation.call_node_id,
                    names=continuation.result_names,
                    steps=steps,
                    transition_ids=transition_ids,
                    depth=state.depth + int(continuation.reverse_boundary),
                    uncertain=uncertain,
                    limitations=tuple(dict.fromkeys((
                        *state.limitations,
                        "返回值传播当前只覆盖由局部变量直接接收的调用结果。",
                    ))),
                    continuations=(
                        state.continuations[:-1]
                        if state.continuations else ()
                    ),
                    boundary_ids=state.boundary_ids,
                ))
        return result

    def _caller_continuations(
        self,
        function: FunctionProgramGraph,
        *,
        transitions: CallTransitionIndex,
        functions_by_symbol: dict[str, list[FunctionProgramGraph]],
        semantic_index: SemanticIndex | None,
    ) -> list[_ReturnContinuation]:
        """为函数内产生的值查找所有静态调用者结果槽位。"""
        result: list[_ReturnContinuation] = []
        for transition in transitions.for_target(function.symbol_id):
            callers = functions_by_symbol.get(transition.source, [])
            overloaded = len(callers) > 1
            for caller in callers:
                hit = self._call_index(caller).get(transition.callsite_id)
                if hit is None:
                    continue
                call_node, call = hit
                result_slot = (
                    semantic_index.call_result(call.callsite_id)
                    if semantic_index is not None else None
                )
                result_names = tuple(dict.fromkeys(
                    result_slot.value_names
                    if result_slot is not None else call_node.definitions
                ))
                result.append(_ReturnContinuation(
                    caller=caller,
                    call_node_id=call_node.node_id,
                    callsite_id=call.callsite_id,
                    result_names=result_names,
                    certainty=(
                        result_slot.certainty if result_slot is not None else "may"
                    ),
                    transition_id=transition.edge_id,
                    uncertain=(
                        transition.certainty != "must"
                        or transition.confidence != "high"
                        or transition.truncated
                        or overloaded
                    ),
                    reverse_boundary=True,
                    result_slots=tuple(call.result_targets),
                ))
        return result

    def _return_step(
        self,
        source: FunctionProgramGraph,
        continuation: _ReturnContinuation,
        return_node: ProgramGraphNode,
        *,
        input_names: tuple[str, ...],
        certainty: str,
    ) -> FlowStepEvidence:
        """记录被调用函数返回槽位到调用者结果槽位的传播。"""
        material = "\x1f".join((
            source.method_id,
            continuation.caller.method_id,
            continuation.callsite_id,
            return_node.node_id,
        ))
        return FlowStepEvidence(
            step_id="flowstep:" + self._digest(material),
            order=0,
            kind="return",
            symbol=source.symbol_id,
            location=self._location(return_node.location),
            expression=f"{source.symbol_id} -> {continuation.caller.symbol_id}",
            input_names=list(input_names),
            output_names=list(continuation.result_names),
            provenance="inferred",
            certainty="may" if certainty == "may" else "must",
            importance="essential",
        )

    def _assignment_steps(
        self,
        function: FunctionProgramGraph,
        path: list[ProgramGraphEdge],
    ) -> list[FlowStepEvidence]:
        """把局部到达定义路径转换为中间赋值步骤，排除终点调用节点。"""
        result: list[FlowStepEvidence] = []
        for edge in path[:-1]:
            node = function.nodes[edge.target]
            if not node.definitions:
                continue
            input_name = edge.source_variable or edge.variable
            output_name = edge.target_variable
            result.append(FlowStepEvidence(
                step_id="flowstep:" + self._digest(node.node_id),
                order=0,
                kind="assignment",
                symbol=function.symbol_id,
                location=self._location(node.location),
                expression=node.code,
                input_names=[input_name] if input_name else node.uses,
                output_names=[output_name] if output_name else node.definitions,
                provenance="inferred",
                certainty=edge.certainty,
            ))
        return result

    def _call_step(
        self,
        source: FunctionProgramGraph,
        target: FunctionProgramGraph,
        call: ProgramCallSite,
        *,
        input_names: tuple[str, ...],
        parameter: str,
        certainty: str,
    ) -> FlowStepEvidence:
        """记录一条实参到目标形参的跨过程值传播边界。"""
        material = "\x1f".join((
            call.callsite_id,
            source.method_id,
            target.method_id,
            parameter,
        ))
        return FlowStepEvidence(
            step_id="flowstep:" + self._digest(material),
            order=0,
            kind="call",
            symbol=source.symbol_id,
            location=self._location(call.location),
            expression=f"{source.symbol_id} -> {target.symbol_id}",
            input_names=list(input_names),
            output_names=[parameter],
            provenance="inferred",
            certainty="may" if certainty == "may" else "must",
            importance="essential",
        )

    def _interprocedural_flow(
        self,
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        state: _FlowState,
        *,
        sink_names: set[str],
        final_path: list[ProgramGraphEdge],
    ) -> DataFlowEvidence:
        """组装一条包含调用边界和最终 Sink 的有界跨过程证据。"""
        steps = list(state.steps)
        steps.extend(self._assignment_steps(state.function, final_path))
        steps.append(self._fact_step(sink, "sink", tuple(sorted(sink_names))))
        steps = [step.model_copy(update={"order": index}) for index, step in enumerate(steps)]
        material = "\x1f".join((
            source.fact_id,
            sink.fact_id,
            *state.transition_ids,
            *state.boundary_ids,
            *(edge.edge_id for edge in final_path),
        ))
        limitations = tuple(dict.fromkeys((
            *state.limitations,
            f"跨过程搜索最多分析 {self.max_call_depth} 层调用或静态值边界。",
            "返回值传播只覆盖局部变量直接接收的调用结果；尚未覆盖字段、数组、集合元素和完整对象别名传播。",
        )))
        uncertain = state.uncertain or any(step.certainty == "may" for step in steps) or any(
            edge.certainty == "may" for edge in final_path
        )
        return DataFlowEvidence(
            flow_id="flow:" + self._digest(material),
            language=state.function.language,
            scope="interprocedural",
            symbol=source.symbol,
            source_fact_id=source.fact_id,
            sink_fact_id=sink.fact_id,
            status="may_reach_sink",
            confidence="low" if uncertain else "medium",
            steps=steps,
            call_edge_ids=list(state.transition_ids),
            value_boundary_ids=list(state.boundary_ids),
            unresolved=list(limitations),
            truncated=False,
        )

    def _source_seed(
        self,
        function: FunctionProgramGraph,
        source: StaticSecurityFact,
        call_index: dict[str, tuple[ProgramGraphNode, ProgramCallSite]],
    ) -> tuple[str, tuple[str, ...]] | None:
        """把参数、调用返回或访问结果 Source 映射到定义节点。"""
        value_flow = source.metadata.get("value_flow") or {}
        if value_flow.get("output_arguments"):
            node = function.nodes.get(CallOutputEffects.node_id(function.method_id, str(source.metadata.get("callsite_id") or "")))
            return (node.node_id, tuple(node.definitions)) if node is not None else None
        if value_flow.get("result") == "parameter":
            entry = function.nodes[function.entry_node_id]
            parameter = source.metadata.get("parameter_seed") or source.name
            if parameter in entry.definitions:
                return entry.node_id, (parameter,)
            return None
        callsite_id = str(source.metadata.get("callsite_id") or "")
        if callsite_id and callsite_id in call_index:
            node, call = call_index[callsite_id]
            if call.result_variable and call.result_variable in node.definitions:
                return node.node_id, (call.result_variable,)
            assigned_targets = source.metadata.get("assigned_targets")
            if isinstance(assigned_targets, list):
                positions = value_flow.get("result_positions")
                if positions:
                    assigned_targets = [assigned_targets[index] for index in positions if isinstance(index, int) and 0 <= index < len(assigned_targets)]
                exact_targets = tuple(
                    name for name in assigned_targets
                    if isinstance(name, str) and name in node.definitions
                )
                return (node.node_id, exact_targets) if exact_targets else None
            return (node.node_id, tuple(node.definitions)) if node.definitions else None
        node = self._containing_node(function, source.location)
        if node is None:
            return None
        targets = source.metadata.get("assigned_targets")
        if isinstance(targets, list):
            names = tuple(name for name in targets if name in node.definitions)
            if names:
                return node.node_id, names
            read_names = tuple(name for name in source.metadata.get("value_identifiers") or [] if name in node.uses)
            return (node.node_id, read_names) if read_names else None
        return node.node_id, tuple(node.definitions)

    def _sink_target(
        self,
        function: FunctionProgramGraph,
        sink: StaticSecurityFact,
        call_index: dict[str, tuple[ProgramGraphNode, ProgramCallSite]],
    ) -> tuple[str, set[str]] | None:
        """只选择规则声明为危险角色的调用实参标识符。"""
        callsite_id = str(sink.metadata.get("callsite_id") or "")
        hit = call_index.get(callsite_id)
        if hit is None:
            if (sink.metadata.get("value_flow") or {}).get("argument_role") == "sink":
                node = self._containing_node(function, sink.location)
                names = set(sink.metadata.get("value_identifiers") or [])
                if node is not None:
                    # 属性写入的值流落在 LHS 定义，而不是 RHS 的变量状态。
                    outputs = {
                        transfer.output_variable for transfer in node.value_transfers
                        if names.intersection(transfer.input_variables)
                    }
                    names = outputs or names.intersection(node.uses)
                return (node.node_id, names) if node is not None and names else None
            return None
        node, call = hit
        value_flow = sink.metadata.get("value_flow") or {}
        names: set[str] = set()
        for position in value_flow.get("arguments") or []:
            if isinstance(position, int) and 0 <= position < len(call.positional_arguments):
                names.update(call.positional_arguments[position])
        for keyword in value_flow.get("keywords") or []:
            names.update(call.keyword_arguments.get(str(keyword), []))
        if value_flow.get("receiver_role") == "sink":
            names.update(call.receiver_identifiers)
        return (node.node_id, names) if names else None

    def _sanitizer_nodes(
        self,
        function: FunctionProgramGraph,
        sanitizers: list[StaticSecurityFact],
        call_index: dict[str, tuple[ProgramGraphNode, ProgramCallSite]],
    ) -> set[str]:
        """返回净化调用所在节点；数据流不得穿过其返回值定义。"""
        result: set[str] = set()
        for sanitizer in sanitizers:
            if (sanitizer.metadata.get("value_flow") or {}).get("result_role") != "sanitized":
                continue
            if not self._contains(function, sanitizer.location):
                continue
            callsite_id = str(sanitizer.metadata.get("callsite_id") or "")
            hit = call_index.get(callsite_id)
            if hit is not None:
                result.add(hit[0].node_id)
        return result

    def _path(
        self,
        function: FunctionProgramGraph,
        *,
        source_node_id: str,
        source_names: tuple[str, ...],
        sink_node_id: str,
        sink_names: set[str],
        blocked_nodes: set[str],
    ) -> list[ProgramGraphEdge] | None:
        """在增强值流 Overlay 上按变量状态寻找最短 Source-to-Sink 路径。"""
        if sink_node_id in blocked_nodes:
            return None
        if source_node_id == sink_node_id and set(source_names).intersection(sink_names):
            successors: dict[str, set[str]] = defaultdict(set)
            for edge in function.edges:
                if edge.kind == "cfg":
                    successors[edge.source].add(edge.target)
            if source_node_id not in ReachingDefinitionsPass._reachable(function.entry_node_id, successors):
                return None
            return []  # 直接 Source-to-Sink 操作，不伪造跨操作边。
        adjacency: dict[str, list[ProgramGraphEdge]] = defaultdict(list)
        value_edges = [edge for edge in function.edges if edge.kind == "value_flow"]
        data_edges = value_edges or [
            edge for edge in function.edges if edge.kind == "reaching_def"
        ]
        for edge in data_edges:
            adjacency[edge.source].append(edge)
        for edges in adjacency.values():
            edges.sort(key=lambda item: (
                item.target,
                item.source_variable or item.variable or "",
                item.target_variable or item.variable or "",
                item.edge_id,
            ))
        initial_names = tuple(dict.fromkeys(source_names)) or tuple(
            function.nodes[source_node_id].definitions
        )
        initial_states = [
            (source_node_id, name)
            for name in initial_names
        ]
        pending = deque(initial_states)
        predecessor: dict[_ValueState, tuple[_ValueState, ProgramGraphEdge]] = {}
        visited = set(initial_states)
        while pending:
            current = pending.popleft()
            current_node, current_name = current
            for edge in adjacency.get(current_node, []):
                if edge.target in blocked_nodes:
                    continue
                source_name = edge.source_variable or edge.variable or current_name
                if source_name != current_name:
                    continue
                target_name = edge.target_variable or edge.variable or current_name
                next_state = (edge.target, target_name)
                if edge.target == sink_node_id and target_name not in sink_names:
                    continue
                if next_state in visited:
                    continue
                predecessor[next_state] = (current, edge)
                if edge.target == sink_node_id:
                    return self._reconstruct(
                        set(initial_states),
                        next_state,
                        predecessor,
                    )
                visited.add(next_state)
                pending.append(next_state)
        return None

    def _flow(
        self,
        function: FunctionProgramGraph,
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        *,
        source_names: tuple[str, ...],
        sink_names: set[str],
        path: list[ProgramGraphEdge],
    ) -> DataFlowEvidence:
        """把值流路径转换为稳定安全证据。"""
        steps = [self._fact_step(source, "source", source_names)]
        steps.extend(self._assignment_steps(function, path))
        steps.append(self._fact_step(sink, "sink", tuple(sorted(sink_names))))
        steps = [step.model_copy(update={"order": index}) for index, step in enumerate(steps)]
        material = "\x1f".join((
            source.fact_id,
            sink.fact_id,
            function.method_id,
            *(edge.edge_id for edge in path),
        ))
        return DataFlowEvidence(
            flow_id="flow:" + self._digest(material),
            language=function.language,
            symbol=source.symbol,
            source_fact_id=source.fact_id,
            sink_fact_id=sink.fact_id,
            status="may_reach_sink",
            confidence="low" if source.metadata.get("binding_certainty") == "may" or any(edge.certainty == "may" for edge in path) else "medium",
            steps=steps,
            unresolved=list(function.limitations),
        )

    @staticmethod
    def _fact_step(
        fact: StaticSecurityFact,
        kind: str,
        names: tuple[str, ...],
    ) -> FlowStepEvidence:
        """构造安全事实端点步骤。"""
        return FlowStepEvidence(
            step_id="flowstep:" + ProgramGraphSecurityFlowAnalyzer._digest(
                fact.fact_id + "\x1f" + kind
            ),
            order=0,
            kind="source" if kind == "source" else "sink",
            symbol=fact.symbol,
            location=fact.location,
            expression=fact.name,
            input_names=[] if kind == "source" else list(names),
            output_names=list(names) if kind == "source" else [],
            certainty="may" if fact.metadata.get("binding_certainty") == "may" else "must",
            importance="essential",
        )

    @staticmethod
    def _call_index(
        function: FunctionProgramGraph,
    ) -> dict[str, tuple[ProgramGraphNode, ProgramCallSite]]:
        """按共享 callsite_id 索引函数内调用点。"""
        return {
            call.callsite_id: (node, call)
            for node in function.nodes.values()
            for call in node.calls
        }

    @staticmethod
    def _contains(
        function: FunctionProgramGraph,
        location: EvidenceLocation,
    ) -> bool:
        """判断安全事实是否位于当前函数源码范围。"""
        scope = function.location
        return (
            scope.path == location.path
            and scope.line <= location.line <= scope.end_line
        )

    @staticmethod
    def _containing_node(
        function: FunctionProgramGraph,
        location: EvidenceLocation,
    ) -> ProgramGraphNode | None:
        """返回范围最小且包含事实位置的操作节点。"""
        candidates = [
            node for node in function.nodes.values()
            if node.location is not None
            and node.kind not in {"method_entry", "method_exit"}
            and node.location.path == location.path
            and (node.location.line, node.location.column) <= (location.line, location.column or 1)
            and (location.end_line or location.line, location.end_column or location.column or 1)
            <= (node.location.end_line, node.location.end_column)
        ]
        return min(candidates, key=lambda item: (
            item.location.end_line - item.location.line,  # type: ignore[union-attr]
            item.location.end_column - item.location.column,  # type: ignore[union-attr]
            item.node_id,
        )) if candidates else None

    @staticmethod
    def _reconstruct(
        sources: set[_ValueState],
        target: _ValueState,
        predecessor: dict[_ValueState, tuple[_ValueState, ProgramGraphEdge]],
    ) -> list[ProgramGraphEdge]:
        """根据带变量身份的 BFS 前驱表重建正向值流边序列。"""
        result: list[ProgramGraphEdge] = []
        current = target
        while current not in sources:
            previous, edge = predecessor[current]
            result.append(edge)
            current = previous
        result.reverse()
        return result

    @staticmethod
    def _location(location: ProgramGraphLocation | None) -> EvidenceLocation:
        """把公共程序图位置转换为安全证据位置。"""
        if location is None:
            raise ValueError("Program graph flow node has no source location")
        return EvidenceLocation(**location.model_dump())

    @staticmethod
    def _digest(value: str) -> str:
        """生成跨运行稳定的紧凑标识。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
