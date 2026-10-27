"""将已经核实的实参、返回和共享值边界交给公共条件路线接口；不推断第三方安全语义。"""

from backend.app.services.program_graph.conditions import ConditionExpression, ConditionPathBinding
from backend.app.services.program_graph.contracts import FunctionProgramGraph, ProgramCallSite, ProgramGraphNode
from backend.app.services.program_graph.path_composition import ConditionRoutePart
from .call_graph import ProgramArgumentBinder
from .value_boundaries import ValueBoundaryIndex, ValueBoundaryTransition


def call_cursor(cursor: ConditionRoutePart, node: ProgramGraphNode, call: ProgramCallSite,
                target: FunctionProgramGraph, binder: ProgramArgumentBinder) -> ConditionRoutePart:
    """复用已确认的直接实参、字段绑定与纯表达式；不从相关变量数猜值相等。"""
    references = binder.bind_references(call, target)
    fields = {**call.argument_field_bindings.get(target.method_id, {}), **(call.receiver_field_bindings or {}).get(target.method_id, {})}
    references.extend((name, value) for name, value in fields.items())
    expressions = binder.bind_conditions(call, target)
    context = target.instance_context
    if context is not None and not context.is_constructor and call.receiver and target.method_id in (call.receiver_field_bindings or {}):
        # 已解析的隐式接收者不是漏传实参；字段复用既有映射，接收者本身仍不当作原始标量。
        expressions.append((context.receiver, ConditionExpression(reason="condition_receiver_object_not_scalar")))
    expression_names = {name for name, _ in expressions}
    references = [(name, value) for name, value in references if name not in expression_names]
    names = {name for name, _ in (*references, *expressions)}
    gaps = []
    missing = set(target.parameters) - names
    if missing:
        gaps.append("condition_arguments_not_bound:" + ",".join(sorted(missing)))
    if any((not parameter.isidentifier() and parameter not in fields) or
           (not name.isidentifier() and name not in node.use_object_slots and name not in node.use_value_ids) for parameter, name in references):
        gaps.append("condition_heap_arguments_not_composed")
    if any(pattern.kind != "name" for pattern in target.parameter_patterns):
        gaps.append("condition_parameter_pattern_not_composed")
    return ConditionRoutePart(function=target, frame_id=cursor.frame_id + "/" + call.callsite_id,
        start_node_id=target.entry_node_id, end_node_id=target.entry_node_id,
        bindings=tuple(ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=node.node_id,
            source_name=name, target_name=parameter) for parameter, name in references) + tuple(
                ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=node.node_id, source_name="", target_name=parameter,
                    expression=expression) for parameter, expression in expressions), gaps=tuple(gaps))


def return_cursor(cursor: ConditionRoutePart, caller: FunctionProgramGraph, call_node_id: str,
                  saved_caller: ConditionRoutePart | None, return_node: ProgramGraphNode,
                  value_names: tuple[str, ...], result_names: tuple[str, ...], expression: ConditionExpression | None = None) -> ConditionRoutePart:
    """沿最近实际调用恢复调用者；直接值或已保存表达式可组合，未知投影不猜相等。"""
    gaps = []
    frame = saved_caller.frame_id if saved_caller is not None else cursor.frame_id + "/reverse:" + call_node_id
    if saved_caller is None:
        gaps.append("condition_reverse_caller_prefix_not_composed")
    direct = len(value_names) == len(result_names) == 1 and any(
        tuple(names) == value_names and form == "reference"
        for names, form in zip(return_node.return_values, return_node.return_value_forms))
    bindings = ()
    if expression is not None and expression.kind != "unknown" and len(result_names) == 1:
        bindings = (ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=return_node.node_id,
            source_name="", target_name=result_names[0], expression=expression),)
    elif direct:
        bindings = (ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=return_node.node_id,
            source_name=value_names[0], target_name=result_names[0]),)
    else:
        gaps.append("condition_return_value_not_exact_copy")
    return ConditionRoutePart(function=caller, frame_id=frame, start_node_id=call_node_id,
        end_node_id=call_node_id, bindings=bindings, skip_start_effects=True, gaps=tuple(gaps))


def boundary_cursor(cursor: ConditionRoutePart, target: FunctionProgramGraph, source_node_id: str,
                    target_node_id: str, source_names: tuple[str, ...], target_names: tuple[str, ...], *,
                    reads_input: bool, kind: str, saved_caller: ConditionRoutePart | None = None,
                    bindings: tuple[ConditionPathBinding, ...] = (), exact: bool = False,
                    skip_node_effects: tuple[str, ...] = ()) -> ConditionRoutePart:
    """保留既有写回/捕获位置和单槽映射；may 堆写或事件次序不能升级为确定条件复制。"""
    frame = saved_caller.frame_id if saved_caller is not None else cursor.frame_id + "/boundary:" + target_node_id
    bindings = bindings or ((ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=source_node_id,
        source_name=source_names[0], target_name=target_names[0], reads_input=reads_input),) if len(source_names) == len(target_names) == 1 else ()
    )
    return ConditionRoutePart(function=target, frame_id=frame, start_node_id=target_node_id, end_node_id=target_node_id,
        bindings=bindings, skip_start_effects=True, skip_node_effects=skip_node_effects,
        gaps=() if exact else ("condition_value_boundary_not_composed:" + kind,))


def writeback_cursor(cursor: ConditionRoutePart, target: FunctionProgramGraph, transition: ValueBoundaryTransition,
                     index: ValueBoundaryIndex, saved_caller: ConditionRoutePart | None) -> ConditionRoutePart:
    """一次带回同一已选正常出口的确定字段/常量元素，未知键、弱写和事件边界仍不闭合。"""
    boundary = transition.boundary
    bindings, skipped = [], []
    exact = False
    for item in index.for_source(cursor.function.method_id) if saved_caller is not None else ():
        value = item.boundary
        reader = target.nodes.get(item.target_node_id)
        if (item.target_method_id != target.method_id or item.source_node_id != transition.source_node_id
                or value.resume_callsite_id != boundary.resume_callsite_id or value.kind != "object_field_writeback"
                or reader is None or reader.certainty != "must" or len(value.source_names) != 1 or len(value.target_names) != 1
                or any("[?]" in name for name in (*value.source_names, *value.target_names))
                or value.target_names[0] not in reader.definition_object_slots):
            continue
        bindings.append(ConditionPathBinding(source_frame=cursor.frame_id, source_node_id=item.source_node_id,
            source_name=value.source_names[0], target_name=value.target_names[0], target_node_id=item.target_node_id,
            reads_input=value.source_reads_input))
        skipped.append(item.target_node_id)
        exact = exact or item.target_node_id == transition.target_node_id
    return boundary_cursor(cursor, target, transition.source_node_id, transition.target_node_id,
        boundary.source_names, boundary.target_names, reads_input=boundary.source_reads_input, kind=boundary.kind,
        saved_caller=saved_caller, bindings=tuple(bindings) if exact else (), exact=exact,
        skip_node_effects=tuple(skipped) if exact else ())
