"""基于公共 ProgramGraph 和已解析调用边的跨语言安全数据流。"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from contextvars import ContextVar
from typing import Any
from backend.app.core.redaction import redact_source

from backend.app.services.program_graph import (
    FunctionProgramGraph,
    ProgramCallSite,
    ProgramGraphArtifact,
    ProgramGraphEdge,
    ProgramGraphLocation,
    ProgramGraphNode,
    ValueFlowQuery,
)
from backend.app.services.semantic_index import CallTransitionIndex, SemanticIndex, SemanticIndexArtifact
from backend.app.services.program_graph.value_paths import ValuePathSearch

from ..contracts import (
    DataFlowEvidence,
    EvidenceLocation,
    FlowStepEvidence,
    SinkArgumentBinding,
    StaticSecurityFact,
)
from ..registry import SemanticsRegistry
from .call_graph import ProgramArgumentBinder
from .value_boundaries import ValueBoundaryIndex
from .argument_slots import prepare_argument_slots
from .call_effects import CallOutputEffects
from .call_results import prepare_call_results
from ..ir import IRValueBoundary
from .diagnostics import FlowDiagnostics
from .objects import prepare_instance_fields
from .return_bindings import result_patterns, return_binding
from backend.app.services.value_binding import BindingPattern
from backend.app.services.value_binding.returns import resolve_return
from backend.app.services.value_binding.identities import value_identity
from backend.app.services.value_binding.objects import legacy_object_allocation_id
from backend.app.services.program_graph.passes.declaration_bindings import DeclarationBindingPass
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.program_graph.conditions import ConditionExpression, ConditionPathBinding, PathConditionProof
from backend.app.services.program_graph.path_conditions import PathConditionQuery
from backend.app.services.program_graph.path_composition import ConditionRoutePart, ConditionRouteQuery
from .condition_routes import call_cursor, return_cursor, writeback_cursor
from .variants import retain_variants


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
    result_patterns: tuple[BindingPattern, ...] = ()
    condition_caller: ConditionRoutePart | None = None


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
    condition_parts: tuple[ConditionRoutePart, ...] = ()
    condition_cursor: ConditionRoutePart | None = None


@dataclass
class _FlowRun:
    """单次分析的私有查询缓存和预算；不得跨项目共享，也不进入持久化产物。"""

    value_search: dict[int, ValuePathSearch] = field(default_factory=dict)
    query_limits: set[tuple[int, str, str]] = field(default_factory=set)
    source_limits: set[str] = field(default_factory=set)
    variant_limits: set[tuple[str, str]] = field(default_factory=set)
    diagnostics: FlowDiagnostics | None = None
    known_api_calls: set[str] = field(default_factory=set)


class ProgramGraphSecurityFlowAnalyzer:
    """把公共 CFG/DDG Overlay 与 Source/Sink 事实连接为安全数据流。"""

    def __init__(
        self,
        *,
        max_call_depth: int = 8,
        max_path_variants: int = 8,
        max_search_states: int = 256,
        semantics: SemanticsRegistry | None = None,
    ) -> None:
        """设置已解析调用参数传播的最大深度，避免递归和大调用图失控。"""
        self.max_call_depth = max(1, max_call_depth)
        self.max_path_variants = max(1, max_path_variants)
        self.max_search_states = max(1, max_search_states)
        self._active_run: ContextVar[_FlowRun | None] = ContextVar("security_flow_run", default=None)
        self.argument_binder = ProgramArgumentBinder(semantics)
        self.path_conditions = PathConditionQuery()

    @property
    def _run(self) -> _FlowRun:
        """查询当前任务状态；调用结束即解除引用，异常也不能残留整张图。"""
        run = self._active_run.get()
        if run is None:
            raise RuntimeError("值流任务上下文不存在")
        return run

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
        diagnostics: FlowDiagnostics | None = None,
    ) -> list[DataFlowEvidence]:
        """返回函数内及有界实参到形参传播能够建立的 Source-to-Sink 路径。"""
        token = self._active_run.set(_FlowRun(diagnostics=diagnostics, known_api_calls={
            str(fact.metadata.get("callsite_id")) for fact in [*sources, *sinks, *sanitizers]
            if fact.metadata.get("callsite_id")}))
        try:
            semantic_view = SemanticIndex.load(semantic_index) if semantic_index is not None else None
            if semantic_view is not None and "declaration_bindings" not in program_graph.overlays and (
                semantic_view.artifact.variable_declarations or semantic_view.artifact.variable_references
            ):
                program_graph = DeclarationBindingPass(semantic_view).project(program_graph)
            if diagnostics is not None:
                for function in program_graph.functions.values():
                    for node in function.nodes.values():
                        for reason in node.selector_diagnostics:
                            diagnostics.record(reason, symbol=function.symbol_id, node_id=node.node_id)
                        for value, reason in node.binding_gaps.items():
                            diagnostics.record("variable_identity_not_mapped", symbol=function.symbol_id,
                                               node_id=node.node_id, value=value, reason_detail=reason)
            transitions = CallTransitionIndex.from_sources(semantic_index=semantic_view, dependency_graph=dependency_graph)
            object_calls = {fact.callsite_id for fact in semantic_view.artifact.callsites.values()
                            if any(target.relation == "instantiates" for target in fact.targets)} if semantic_view else set()
            object_calls.update(str(edge.get("callsite_id") or "") for edge in (dependency_graph or {}).get("links", [])
                                if edge.get("relation") == "instantiates")
            program_graph = prepare_call_results(program_graph, sources, transitions, object_calls=object_calls, diagnostics=diagnostics)
            program_graph = CallOutputEffects.prepare(program_graph, sources)
            program_graph, initializer_boundaries = prepare_instance_fields(program_graph, semantic_view, self.argument_binder, self._path, diagnostics)
            program_graph = prepare_argument_slots(program_graph, transitions, self.argument_binder)
            boundary_index = ValueBoundaryIndex(program_graph, [*(value_boundaries or []), *initializer_boundaries])
            program_graph = boundary_index.graph
            # 所有私有对象/框架值槽编译完成后只选择一次；不让后置公共 Pass 覆盖原生结果。
            program_graph = ValueFlowQuery(semantic_view).project(program_graph)
            if diagnostics is not None:
                diagnostics.value_backends = {function.method_id: {"symbol": function.symbol_id,
                    **function.value_backend.model_dump()} for function in program_graph.functions.values()}
            functions_by_symbol: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
            for function in program_graph.functions.values():
                functions_by_symbol[function.symbol_id].append(function)
            sinks_by_symbol: dict[str, list[StaticSecurityFact]] = defaultdict(list)
            for sink in sinks:
                sinks_by_symbol[sink.symbol].append(sink)
            # 净化名称匹配是观察，不是安全证明。未经值、上下文和时序验证，
            # 不得把整个操作节点当作 kill 屏障；事实仍由证据服务保留。

            flows: list[DataFlowEvidence] = []
            for source in sources:
                mapped = False
                for function in functions_by_symbol.get(source.symbol, []):
                    if not self._contains(function, source.location):
                        continue
                    call_index = self._call_index(function)
                    source_seed = self._source_seed(function, source, call_index)
                    if source_seed is None:
                        continue
                    mapped = True
                    for sink in sinks_by_symbol.get(source.symbol, []):
                        if not self._contains(function, sink.location):
                            continue
                        sink_target = self._sink_target(function, sink, call_index)
                        if sink_target is None:
                            continue
                        sink_node_id, sink_names, reads_input = sink_target
                        for path in self._paths(
                            function,
                            source_node_id=source_seed[0],
                            source_names=source_seed[1],
                            sink_node_id=sink_node_id,
                            sink_names=sink_names,
                            reads_input=reads_input,
                        ):
                            flows.append(self._flow(
                                function,
                                source,
                                sink,
                                source_names=source_seed[1],
                                sink_names=sink_names,
                                path=path,
                                reads_input=reads_input,
                            ))
                if not mapped and diagnostics is not None:
                    diagnostics.record("source_seed_not_mapped", fact_id=source.fact_id,
                                       symbol=source.symbol, **source.location.model_dump(exclude_none=True))
            if diagnostics is not None:
                for sink in sinks:
                    if not any(self._sink_target(function, sink, self._call_index(function)) is not None
                               for function in functions_by_symbol.get(sink.symbol, []) if self._contains(function, sink.location)):
                        diagnostics.record("sink_without_trackable_value", fact_id=sink.fact_id,
                                           symbol=sink.symbol, **sink.location.model_dump(exclude_none=True))
            if dependency_graph is not None or semantic_view is not None or value_boundaries:
                flows.extend(self._interprocedural_flows(
                    transitions=transitions,
                    semantic_index=semantic_view,
                    sources=sources,
                    sinks_by_symbol=sinks_by_symbol,
                    functions_by_symbol=functions_by_symbol,
                    boundary_index=boundary_index,
                    diagnostics=diagnostics,
                ))
            return retain_variants(flows, limit=self.max_path_variants, source_limits=self._run.source_limits,
                                   variant_limits=self._run.variant_limits, diagnostics=diagnostics)
        finally:
            self._active_run.reset(token)


    def _interprocedural_flows(
        self,
        *,
        transitions: CallTransitionIndex,
        semantic_index: SemanticIndex | None,
        sources: list[StaticSecurityFact],
        sinks_by_symbol: dict[str, list[StaticSecurityFact]],
        functions_by_symbol: dict[str, list[FunctionProgramGraph]],
        boundary_index: ValueBoundaryIndex,
        diagnostics: FlowDiagnostics | None,
    ) -> list[DataFlowEvidence]:
        """沿确定调用点把实参绑定到目标形参，搜索有限深度的跨函数路径。"""
        results: list[DataFlowEvidence] = []
        pair_counts: dict[tuple[str, str], int] = defaultdict(int)
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
            visited: set[tuple[Any, ...]] = set()
            while queue:
                remaining = self.max_search_states - len(visited)
                if len(queue) > remaining:
                    if source.fact_id not in self._run.source_limits and diagnostics is not None:
                        diagnostics.record("route_search_state_limit", source_fact_id=source.fact_id, limit=self.max_search_states)
                    self._run.source_limits.add(source.fact_id)
                    while len(queue) > remaining:
                        queue.pop()
                if not queue:
                    break
                state = queue.popleft()
                visit_key = (
                    state.function.method_id,
                    state.node_id,
                    state.names,
                    tuple(item.callsite_id for item in state.continuations),
                    tuple(step.step_id for step in state.steps),
                    state.transition_ids, state.boundary_ids,
                )
                if visit_key in visited:
                    continue
                visited.add(visit_key)
                call_index = self._call_index(state.function)
                for sink in (sinks_by_symbol.get(state.function.symbol_id, []) if state.transition_ids or state.boundary_ids else []):
                    pair = (source.fact_id, sink.fact_id)
                    if not self._contains(state.function, sink.location):
                        continue
                    if pair_counts[pair] >= self.max_path_variants + 1:
                        self._run.variant_limits.add(pair)
                        continue
                    target = self._sink_target(state.function, sink, call_index)
                    if target is None:
                        continue
                    sink_node_id, sink_names, reads_input = target
                    for local_path in self._paths(
                        state.function,
                        source_node_id=state.node_id,
                        source_names=state.names,
                        sink_node_id=sink_node_id,
                        sink_names=sink_names,
                        reads_input=reads_input,
                    ):
                        flow = self._interprocedural_flow(
                            source,
                            sink,
                            state,
                            sink_names=sink_names,
                            final_path=local_path,
                            reads_input=reads_input,
                        )
                        results.append(flow)
                        pair_counts[pair] += 1
                queue.extend(self._return_states(
                    state,
                    semantic_index=semantic_index,
                    transitions=transitions,
                    functions_by_symbol=functions_by_symbol,
                ))
                queue.extend(self._boundary_states(state, boundary_index, returning=True))
                if state.depth >= self.max_call_depth:
                    if diagnostics is not None:
                        diagnostics.record("call_depth_limit", source_fact_id=source.fact_id,
                                           symbol=state.function.symbol_id, node_id=state.node_id,
                                           depth=state.depth, limit=self.max_call_depth)
                    continue
                if diagnostics is not None and semantic_index is not None:
                    self._record_unresolved_calls(state, source, semantic_index, call_index, boundary_index, diagnostics,
                                                  modelled_symbols=set(functions_by_symbol))
                queue.extend(self._boundary_states(state, boundary_index))
                # 按真实调用位置稳定核验，不让哈希 ID 决定同端点哪条路线先占用预算。
                for transition in sorted(transitions.for_source(state.function.symbol_id), key=lambda item: (
                    call_index[item.callsite_id][1].location.line, call_index[item.callsite_id][1].location.column, item.edge_id
                ) if item.callsite_id in call_index else (2 ** 63, 0, item.edge_id)):
                    hit = call_index.get(transition.callsite_id)
                    if hit is None:
                        if diagnostics is not None:
                            diagnostics.record("callsite_not_mapped", source_fact_id=source.fact_id,
                                               symbol=state.function.symbol_id, callsite_id=transition.callsite_id)
                        continue
                    call_node, call = hit
                    target_functions = functions_by_symbol.get(transition.target, [])
                    if not target_functions and diagnostics is not None and not transition.target.startswith("<"):
                        diagnostics.record("call_target_not_modelled", source_fact_id=source.fact_id,
                                           symbol=state.function.symbol_id, callsite_id=call.callsite_id,
                                           target=transition.target, **call.location.model_dump())
                    overloaded = len(target_functions) > 1
                    for target_function in target_functions:
                        for parameter, argument_names in self.argument_binder.bind(
                            call,
                            target_function,
                        ):
                            for local_path in self._paths(
                                state.function,
                                source_node_id=state.node_id,
                                source_names=state.names,
                                sink_node_id=call_node.node_id,
                                sink_names=set(argument_names),
                            ):
                                uncertain = (
                                    state.uncertain
                                    or transition.certainty != "must"
                                    or transition.confidence != "high"
                                    or transition.truncated
                                    or self.argument_binder.is_uncertain(call, target_function)
                                    or overloaded
                                    or any(pattern.kind != "name" for pattern in target_function.parameter_patterns)
                                    or any(edge.certainty == "may" for edge in local_path)
                                )
                                steps = list(state.steps)
                                steps.extend(self._assignment_steps(
                                    state.function,
                                    local_path,
                                ))
                                steps.extend(self._field_alias_steps(state.function, call_node.node_id, set(argument_names)))
                                steps.extend(self._alias_steps(call, target_function))
                                steps.append(self._call_step(
                                    state.function,
                                    target_function,
                                    call,
                                    input_names=self._reached_names(local_path, state.names, set(argument_names)),
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
                                result_names = self._result_names(call_node, call, semantic_index)
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
                                    result_patterns=result_patterns(call, result_names),
                                    condition_caller=self._condition_cursor(state),
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
                                    condition_parts=state.condition_parts + (self._condition_cursor(state).finish(call_node.node_id, local_path, before_call=True),),
                                    condition_cursor=call_cursor(self._condition_cursor(state), call_node, call, target_function, self.argument_binder),
                                ))
        return results

    def _record_unresolved_calls(
        self, state: _FlowState, source: StaticSecurityFact, index: SemanticIndex,
        calls: dict[str, tuple[ProgramGraphNode, ProgramCallSite]],
        boundaries: ValueBoundaryIndex, diagnostics: FlowDiagnostics,
        *, modelled_symbols: set[str],
    ) -> None:
        """只定位已追踪值实际抵达的未解析调用，不把函数内全部外部 API 当断点。"""
        modelled = {item.boundary.source_location.location_id for item in boundaries.for_source(state.function.method_id)}
        for fact in index.calls_from(state.function.symbol_id):
            if fact.callsite_id in self._run.known_api_calls:
                continue
            if fact.callsite_id in state.function.instance_allocations:
                continue
            constructor = bool(fact.targets) and all(item.relation == "instantiates" for item in fact.targets)
            missing_body = bool(fact.targets) and any(target.target_symbol not in modelled_symbols for target in fact.targets)
            if (fact.targets and not constructor and not missing_body) or (
                not constructor and not fact.unresolved_reason and not missing_body) or fact.location.location_id in modelled:
                continue
            hit = calls.get(fact.callsite_id)
            if hit is None:
                continue
            node, call = hit
            arguments = {name for names in (*call.positional_arguments, *call.keyword_arguments.values()) for name in names}
            if not arguments:
                continue
            path = self._path(state.function, source_node_id=state.node_id, source_names=state.names,
                              sink_node_id=node.node_id, sink_names=arguments)
            if path is not None:
                prefix = [*state.steps, *self._assignment_steps(state.function, path)]
                diagnostics.record("constructor_heap_effect_not_modelled" if constructor else
                                   "external_api_semantics_unknown" if missing_body else "unresolved_call_with_tracked_argument", source_fact_id=source.fact_id,
                                   callsite_id=call.callsite_id, symbol=state.function.symbol_id,
                                   unresolved_reason=fact.unresolved_reason, name=fact.name,
                                   binding_diagnostics=fact.binding_diagnostics,
                                   value_names=list(self._reached_names(path, state.names, arguments)),
                                   target_symbols=[target.target_symbol for target in fact.targets], return_relation="unknown",
                                   known_prefix=[step.model_dump(exclude={"expression"}) for step in prefix[:64]],
                                   prefix_steps_omitted=max(0, len(prefix) - 64),
                                   **call.location.model_dump())

    def _boundary_states(
        self, state: _FlowState, index: ValueBoundaryIndex, *, returning: bool = False,
    ) -> list[_FlowState]:
        """复用局部值流验证写入，再经 may 边界进入读取侧；共用配置的传播预算。"""
        result: list[_FlowState] = []
        for transition in index.for_source(state.function.method_id):
            boundary = transition.boundary
            if bool(boundary.resume_callsite_id) != returning:
                continue
            if returning and not state.continuations and state.depth >= self.max_call_depth:
                continue
            if boundary.resume_callsite_id and state.continuations and state.continuations[-1].callsite_id != boundary.resume_callsite_id:
                continue
            for path in self._paths(
                state.function, source_node_id=state.node_id, source_names=state.names,
                sink_node_id=transition.source_node_id, sink_names=set(boundary.source_names),
                reads_input=boundary.source_reads_input,
            ):
                target = index.graph.functions[transition.target_method_id]
                writer = state.function.nodes[transition.source_node_id]
                reader = target.nodes[transition.target_node_id]
                steps = [*state.steps, *self._assignment_steps(state.function, path, include_terminal=not boundary.source_reads_input)]
                for suffix, function, location, expression in (
                    ("write", state.function, boundary.source_location, f"{boundary.kind}: {', '.join(boundary.source_names)}" if boundary.source_at_exit else writer.code),
                    ("read", target, boundary.target_location, f"{boundary.kind}: {', '.join(boundary.target_names)}" if boundary.capture else reader.code),
                ):
                    steps.append(FlowStepEvidence(
                        step_id="flowstep:" + self._digest(boundary.boundary_id + suffix),
                        order=0, kind="assignment", symbol=function.symbol_id,
                        location=EvidenceLocation(**location.__dict__), expression=redact_source(expression),
                        input_names=list(boundary.source_names if suffix == "write" else boundary.target_names),
                        output_names=list(boundary.target_names), provenance="inferred", certainty="may",
                        importance="essential",
                        reference_edge_id=boundary.reference_edge_id, callsite_id=boundary.callsite_id,
                        object_allocation_id=boundary.object_allocation_id,
                        object_type_symbol=boundary.object_type_symbol,
                    ))
                result.append(_FlowState(
                    function=target, node_id=transition.target_node_id, names=boundary.target_names,
                    steps=steps, transition_ids=state.transition_ids,
                    depth=state.depth + int(not (boundary.resume_callsite_id and state.continuations)),
                    uncertain=True, boundary_ids=state.boundary_ids + (boundary.boundary_id,),
                    continuations=(state.continuations[:-1] if state.continuations else ()) if boundary.resume_callsite_id else (),
                    limitations=tuple(dict.fromkeys((*state.limitations, *target.limitations, *boundary.limitations))),
                    condition_parts=state.condition_parts + (self._condition_cursor(state).finish(transition.source_node_id, path),),
                    condition_cursor=writeback_cursor(self._condition_cursor(state), target, transition, index,
                        state.continuations[-1].condition_caller if boundary.resume_callsite_id and state.continuations else None),
                ))
        return result

    def _return_states(
        self,
        state: _FlowState,
        *,
        semantic_index: SemanticIndex | None,
        transitions: CallTransitionIndex,
        functions_by_symbol: dict[str, list[FunctionProgramGraph]],
    ) -> list[_FlowState]:
        """把可达 return 值映射回最近调用点的结果变量。"""
        if state.function.instance_context is not None and state.function.instance_context.is_constructor:
            return []
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
                if node is not None and node.kind == "return":
                    names = tuple(dict.fromkeys(name for part in node.return_values for name in part)) if node.return_values else tuple(fact.value_names)
                    if names:
                        return_items.append((node, names, fact.certainty))
        if not return_items:
            return_items = [
                (node, tuple(dict.fromkeys(name for part in node.return_values for name in part)) if node.return_values else tuple(node.uses), "may")
                for node in state.function.nodes.values()
                if node.kind == "return" and node.uses
            ]
        result: list[_FlowState] = []
        for base_continuation in continuations:
            for return_node, _, return_certainty in return_items:
                for pattern in base_continuation.result_patterns:
                    resolution = resolve_return(return_binding(return_node), pattern)
                    for projection in resolution.projections:
                        continuation = replace(
                            base_continuation, result_names=(projection.output,),
                            uncertain=base_continuation.uncertain or projection.certainty == "may" or bool(resolution.diagnostics),
                        )
                        result.extend(self._return_value_states(
                            state, continuation, return_node, projection.inputs, return_certainty, projection.expression,
                            tuple((item.output, item.expression) for item in resolution.projections if item.expression is not None),
                        ))
        return result

    def _return_value_states(
        self, state: _FlowState, continuation: _ReturnContinuation,
        return_node: ProgramGraphNode, value_names: tuple[str, ...], return_certainty: str,
        expression: ConditionExpression | None = None,
        condition_results: tuple[tuple[str, ConditionExpression], ...] = (),
    ) -> list[_FlowState]:
        """将一个已选定返回分量映射到对应接收槽位，复用相同局部路径验证。"""
        if not value_names:
            return []
        result: list[_FlowState] = []
        for path in self._paths(
            state.function, source_node_id=state.node_id, source_names=state.names,
            sink_node_id=return_node.node_id, sink_names=set(value_names),
        ):
            uncertain = (
                state.uncertain or continuation.uncertain or continuation.certainty != "must"
                or return_certainty != "must" or any(edge.certainty == "may" for edge in path)
            )
            steps = [*state.steps, *self._assignment_steps(state.function, path)]
            steps.extend(self._field_alias_steps(state.function, return_node.node_id, set(value_names)))
            steps.append(self._return_step(
                state.function, continuation, return_node,
                input_names=self._reached_names(path, state.names, set(value_names)),
                certainty="may" if uncertain else "must",
            ))
            transition_ids = state.transition_ids
            if continuation.transition_id not in transition_ids:
                transition_ids += (continuation.transition_id,)
            cursor = return_cursor(self._condition_cursor(state), continuation.caller, continuation.call_node_id,
                continuation.condition_caller, return_node, value_names, continuation.result_names, expression)
            if condition_results:
                cursor = replace(cursor, bindings=tuple(ConditionPathBinding(source_frame=self._condition_cursor(state).frame_id,
                    source_node_id=return_node.node_id, source_name="", target_name=name, expression=value) for name, value in condition_results))
            result.append(_FlowState(
                function=continuation.caller, node_id=continuation.call_node_id,
                names=continuation.result_names, steps=steps, transition_ids=transition_ids,
                depth=state.depth + int(continuation.reverse_boundary), uncertain=uncertain,
                limitations=tuple(dict.fromkeys((
                    *state.limitations,
                    "返回值传播覆盖局部接收和精确实参槽位；不证明任意表达式或堆别名。",
                ))),
                continuations=state.continuations[:-1] if state.continuations else (),
                boundary_ids=state.boundary_ids,
                condition_parts=state.condition_parts + (self._condition_cursor(state).finish(return_node.node_id, path),),
                condition_cursor=cursor,
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
                result_names = self._result_names(call_node, call, semantic_index)
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
                    result_patterns=result_patterns(call, result_names),
                ))
        return result

    @staticmethod
    def _result_names(
        node: ProgramGraphNode, call: ProgramCallSite, index: SemanticIndex | None,
    ) -> tuple[str, ...]:
        """优先精确调用结果槽位，禁止把兄弟嵌套调用的定义混入返回落点。"""
        if call.result_variable:
            return (call.result_variable,)
        if call.result_targets:
            return tuple(name for name in call.result_targets if name)
        slot = index.call_result(call.callsite_id) if index is not None else None
        return tuple(dict.fromkeys(slot.value_names if slot is not None else node.definitions))

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
        *, include_terminal: bool = False,
    ) -> list[FlowStepEvidence]:
        """展开局部赋值证据；默认排除 Sink 终点，写回型值边界保留终点定义。"""
        result: list[FlowStepEvidence] = []
        for edge in path if include_terminal else path[:-1]:
            node = function.nodes[edge.target]
            if not node.definitions:
                continue
            result.extend(self._field_alias_steps(function, node.node_id,
                {name for name in (edge.read_variable, edge.target_variable) if name}))
            effects = [effect for effect in function.field_effects if effect.node_id == node.node_id
                       and effect.output_name == edge.target_variable
                       and (edge.read_variable or edge.source_variable) in effect.input_names]
            if effects:
                effect = effects[0]
                common = {"object_allocation_id": effect.allocation_id, "callsite_id": effect.callsite_id,
                          "object_type_symbol": effect.object_type_symbol,
                          "reference_edge_id": effect.reference_edge_id, "provenance": "inferred", "certainty": "may"}
                result.append(FlowStepEvidence(step_id="flowstep:" + self._digest(effect.allocation_id + effect.parameter),
                              order=0, kind="call", symbol=function.symbol_id, location=self._location(effect.location),
                              expression=f"实例初始化 → {effect.constructor_symbol}", input_names=effect.input_names,
                              output_names=[effect.parameter], **common))
                result.extend(FlowStepEvidence(step_id="flowstep:" + self._digest(effect.allocation_id + origin.location.location_id),
                              order=0, kind="assignment", symbol=origin.symbol, location=self._location(origin.location),
                              expression=redact_source(origin.expression), input_names=origin.input_names, output_names=origin.output_names,
                              **common) for origin in effect.origins)
                result.append(FlowStepEvidence(step_id="flowstep:" + self._digest(effect.allocation_id + effect.output_name),
                              order=0, kind="assignment", symbol=function.symbol_id, location=self._location(effect.location),
                              expression=f"实例字段绑定：{effect.field_name} → {effect.output_name}", input_names=[effect.field_name],
                              output_names=[effect.output_name], **common))
                continue
            input_name = edge.source_variable or edge.variable
            output_name = edge.target_variable
            result.append(FlowStepEvidence(
                step_id="flowstep:" + self._digest(node.node_id),
                order=0,
                kind="assignment",
                symbol=function.symbol_id,
                location=self._location(node.location),
                expression=redact_source(node.code),
                input_names=[input_name] if input_name else node.uses,
                output_names=[output_name] if output_name else node.definitions,
                provenance="inferred",
                certainty=edge.certainty,
            ))
        return result

    def _alias_steps(self, call: ProgramCallSite, target: FunctionProgramGraph) -> list[FlowStepEvidence]:
        """为接收者别名保留真实赋值位置；这是 may 身份证据，不是复制整对象的污点边。"""
        return self._alias_origin_steps(call.receiver_alias_origins, call.receiver_allocation_id, call.callsite_id,
            target.instance_context.owner_symbol if target.instance_context is not None else None)

    def _field_alias_steps(self, function: FunctionProgramGraph, node_id: str, names: set[str]) -> list[FlowStepEvidence]:
        """字段访问同样交付别名来源，不能只在对象方法接收者中保留 a→b 的身份关系。"""
        node = function.nodes[node_id]
        result = []
        for name in sorted(names):
            origins = node.object_alias_origins.get(name, ())
            write = name in node.definitions
            slot = (node.definition_object_slots if write else node.use_object_slots).get(name)
            allocation_id = slot.allocation_id if slot is not None else legacy_object_allocation_id(value_identity(node, name, write=write))
            if origins and allocation_id is not None:
                result.extend(self._alias_origin_steps(list(origins), allocation_id, node_id + name))
        return result

    def _alias_origin_steps(self, origins: list[FieldValueOrigin], allocation_id: str,
                           context: str, owner: str | None = None) -> list[FlowStepEvidence]:
        """将既有真实赋值位置转为可追溯 may 证据，字段与接收者共用一种序列化方式。"""
        return [FlowStepEvidence(step_id="flowstep:" + self._digest(context + origin.location.location_id + (origin.callsite_id or "")),
            order=0, kind="assignment", symbol=origin.symbol, location=self._location(origin.location),
            expression=redact_source(origin.expression), input_names=origin.input_names,
            output_names=origin.output_names, provenance="inferred", certainty="may", importance="essential",
            object_allocation_id=allocation_id or None, object_type_symbol=origin.object_type_symbol or owner,
            reference_edge_id=origin.reference_edge_id, callsite_id=origin.callsite_id) for origin in origins]

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
        reads_input: bool,
    ) -> DataFlowEvidence:
        """组装一条包含调用边界和最终 Sink 的有界跨过程证据。"""
        steps = list(state.steps)
        steps.extend(self._assignment_steps(state.function, final_path))
        sink_node = state.function.nodes[final_path[-1].target] if final_path else self._containing_node(state.function, sink.location)
        if sink_node is not None:
            steps.extend(self._field_alias_steps(state.function, sink_node.node_id, sink_names))
        steps.append(self._sink_step(state.function, sink, self._reached_names(final_path, state.names, sink_names, reads_input=reads_input)))
        steps = [step.model_copy(update={"order": index}) for index, step in enumerate(steps)]
        material = "\x1f".join((
            source.fact_id,
            sink.fact_id,
            *state.transition_ids,
            *state.boundary_ids,
            *(step.step_id for step in steps),
            *(edge.edge_id for part in state.condition_parts for edge in part.value_edges),
            *(edge.edge_id for edge in final_path),
        ))
        limitations = tuple(dict.fromkeys((
            *state.limitations,
            f"跨过程搜索最多分析 {self.max_call_depth} 层调用或静态值边界。",
            "返回值传播覆盖局部接收和精确实参槽位；尚未覆盖完整字段、集合和对象别名传播。",
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
            call_edge_ids=list(dict.fromkeys((*state.transition_ids, *(step.reference_edge_id for step in steps if step.reference_edge_id)))),
            value_boundary_ids=list(state.boundary_ids),
            path_conditions=[ConditionRouteQuery(self.path_conditions).prove(state.condition_parts + (
                self._condition_cursor(state).finish(sink_node.node_id if sink_node else "", final_path),))],
            unresolved=list(limitations),
            truncated=any((id(part.function), part.start_node_id, part.end_node_id) in self._run.query_limits
                for part in (*state.condition_parts, self._condition_cursor(state).finish(sink_node.node_id if sink_node else "", final_path))),
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
                context = function.instance_context
                assigned_targets = [name if name in node.definitions or context is None else
                                    context.receiver + "." + name for name in assigned_targets]
                positions = value_flow.get("result_positions")
                if positions:
                    assigned_targets = [assigned_targets[index] for index in positions if isinstance(index, int) and 0 <= index < len(assigned_targets)]
                exact_targets = tuple(
                    name for name in assigned_targets
                    if isinstance(name, str) and name in node.definitions
                )
                if not exact_targets and node.access_selectors:
                    # 键投影会改变 LHS 名称；只使用本 Source 调用实际写入的传递，不能把键中的调用当元素 Source。
                    exact_targets = tuple(dict.fromkeys(transfer.output_variable for transfer in node.value_transfers
                        if call.callsite_id in transfer.callsite_ids and transfer.output_variable in node.definitions))
                return (node.node_id, exact_targets) if exact_targets else None
            return (node.node_id, tuple(node.definitions)) if node.definitions else None
        node = self._containing_node(function, source.location)
        if node is None:
            return None
        targets = source.metadata.get("assigned_targets")
        if isinstance(targets, list):
            # 属性 Source 是本操作读取的输入，不只是接收外层调用结果的变量。
            # 已解析项目调用的返回落点不得直接作为 Source，必须经真实 return。
            pending_returns = {
                transfer.output_variable for transfer in node.value_transfers
                if transfer.transfer_kind == "call_result" and not transfer.input_variables
            }
            names = tuple(name for name in targets if name in node.definitions and name not in pending_returns)
            read_names = tuple(name for name in source.metadata.get("value_identifiers") or [] if name in node.uses)
            seeds = tuple(dict.fromkeys((*read_names, *names)))
            return (node.node_id, seeds) if seeds else None
        return (node.node_id, tuple(node.definitions)) if node.definitions else None

    def _sink_target(
        self,
        function: FunctionProgramGraph,
        sink: StaticSecurityFact,
        call_index: dict[str, tuple[ProgramGraphNode, ProgramCallSite]],
    ) -> tuple[str, set[str], bool] | None:
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
                return (node.node_id, names, False) if node is not None and names else None
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
        return (node.node_id, names, True) if names else None

    def _search(self, function: FunctionProgramGraph) -> ValuePathSearch:
        """按最终只读函数实例复用邻接表；私有 Overlay 改变时不复用旧图。"""
        if self._active_run.get() is None:
            return ValuePathSearch(function)
        key = id(function)
        if key not in self._run.value_search:
            self._run.value_search[key] = ValuePathSearch(function)
        return self._run.value_search[key]

    def _path(self, function: FunctionProgramGraph, *, source_node_id: str, source_names: tuple[str, ...],
              sink_node_id: str, sink_names: set[str], reads_input: bool = True) -> list[ProgramGraphEdge] | None:
        """摘要生产者仍查询一条路线；与多路径消费者共享唯一值查询实现，不伪称穷尽。"""
        result = self._search(function).search(source_node_id=source_node_id, source_names=source_names,
            sink_node_id=sink_node_id, sink_names=sink_names, reads_input=reads_input, limit=1, probe_extra=False)
        return list(result.paths[0]) if result.paths else None

    def _paths(self, function: FunctionProgramGraph, *, source_node_id: str, source_names: tuple[str, ...],
               sink_node_id: str, sink_names: set[str], reads_input: bool = True) -> list[list[ProgramGraphEdge]]:
        """沿不同值边保留较长变体；额外探测一条用于标记保留上限，不穷举循环。"""
        result = self._search(function).search(source_node_id=source_node_id, source_names=source_names,
            sink_node_id=sink_node_id, sink_names=sink_names, reads_input=reads_input, limit=self.max_path_variants + 1)
        if result.truncated:
            self._run.query_limits.add((id(function), source_node_id, sink_node_id))
            if self._run.diagnostics is not None:
                self._run.diagnostics.record("value_path_search_limit", symbol=function.symbol_id,
                    node_id=sink_node_id, explored_states=result.explored_states)
        return [list(path) for path in result.paths]

    def _flow(
        self,
        function: FunctionProgramGraph,
        source: StaticSecurityFact,
        sink: StaticSecurityFact,
        *,
        source_names: tuple[str, ...],
        sink_names: set[str],
        path: list[ProgramGraphEdge],
        reads_input: bool,
    ) -> DataFlowEvidence:
        """把值流路径转换为稳定安全证据。"""
        steps = [self._fact_step(source, "source", source_names)]
        steps.extend(self._assignment_steps(function, path))
        sink_node = function.nodes[path[-1].target] if path else self._containing_node(function, sink.location)
        if sink_node is not None:
            steps.extend(self._field_alias_steps(function, sink_node.node_id, sink_names))
        steps.append(self._sink_step(function, sink, self._reached_names(path, source_names, sink_names, reads_input=reads_input)))
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
            scope="interprocedural" if any(step.object_allocation_id for step in steps) else "intra_procedural",
            symbol=source.symbol,
            source_fact_id=source.fact_id,
            sink_fact_id=sink.fact_id,
            status="may_reach_sink",
            confidence="low" if source.metadata.get("binding_certainty") == "may" or any(edge.certainty == "may" for edge in path) else "medium",
            steps=steps,
            call_edge_ids=list(dict.fromkeys(step.reference_edge_id for step in steps if step.reference_edge_id)),
            path_conditions=[self._condition_proof(function,
                path[0].source if path else sink_node.node_id if sink_node else "",
                sink_node.node_id if sink_node else "", path)],
            unresolved=list(function.limitations),
            truncated=(id(function), path[0].source if path else sink_node.node_id if sink_node else "",
                       sink_node.node_id if sink_node else "") in self._run.query_limits,
        )

    @staticmethod
    def _condition_cursor(state: _FlowState) -> ConditionRoutePart:
        """首次查询从 Source 所在函数入口开始，后续复用准确的调用激活及恢复位置。"""
        return state.condition_cursor or ConditionRoutePart(function=state.function, frame_id="root:" + state.function.method_id,
            start_node_id=state.node_id, end_node_id=state.node_id)

    def _condition_proof(self, function: FunctionProgramGraph, source_node_id: str, sink_node_id: str,
                         path: list[ProgramGraphEdge]) -> PathConditionProof:
        """对已经选中的值流路线核验共同 CFG 条件；不把未知或矛盾结果改写为安全结论。"""
        points = [source_node_id, *(edge.target for edge in path)]
        if points[-1] != sink_node_id:
            points.append(sink_node_id)
        return self.path_conditions.prove(function, points, value_edges=path)

    @staticmethod
    def _reached_names(
        path: list[ProgramGraphEdge], seed: tuple[str, ...], targets: set[str],
        *, reads_input: bool = True,
    ) -> tuple[str, ...]:
        """只声明本次路径实际到达的变量，不把其他敏感实参一起标记为已传播。"""
        if path:
            last = path[-1]
            name = (last.read_variable or last.target_variable or last.variable) if reads_input else (last.target_variable or last.variable)
            return tuple(sorted({name or ""} & targets))
        return tuple(sorted(set(seed) & targets))

    def _sink_step(
        self, function: FunctionProgramGraph, sink: StaticSecurityFact, names: tuple[str, ...],
    ) -> FlowStepEvidence:
        """解引用规则与实际调用，保存已到达的位置、关键字或接收者槽位。"""
        step = self._fact_step(sink, "sink", names)
        hit = self._call_index(function).get(str(sink.metadata.get("callsite_id") or ""))
        bindings: list[SinkArgumentBinding] = []
        role = sink.metadata.get("value_flow") or {}
        reached = set(names)
        if hit is not None:
            call = hit[1]
            for position in role.get("arguments") or []:
                if isinstance(position, int) and 0 <= position < len(call.positional_arguments):
                    values = sorted(reached.intersection(call.positional_arguments[position]))
                    if values:
                        bindings.append(SinkArgumentBinding(kind="position", position=position, value_names=values))
            for keyword in role.get("keywords") or []:
                values = sorted(reached.intersection(call.keyword_arguments.get(keyword, [])))
                if values:
                    bindings.append(SinkArgumentBinding(kind="keyword", keyword=keyword, value_names=values))
            if role.get("receiver_role") == "sink":
                values = sorted(reached.intersection(call.receiver_identifiers))
                if values:
                    bindings.append(SinkArgumentBinding(kind="receiver", value_names=values))
        elif names:
            bindings.append(SinkArgumentBinding(kind="value", value_names=list(names)))
        return step.model_copy(update={"argument_bindings": bindings})

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
    def _location(location: ProgramGraphLocation | None) -> EvidenceLocation:
        """把公共程序图位置转换为安全证据位置。"""
        if location is None:
            raise ValueError("Program graph flow node has no source location")
        return EvidenceLocation(**location.model_dump())

    @staticmethod
    def _digest(value: str) -> str:
        """生成跨运行稳定的紧凑标识。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
