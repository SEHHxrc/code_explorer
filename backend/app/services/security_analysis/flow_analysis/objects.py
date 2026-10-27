"""有限实例字段 Overlay：所有语言共用，不解析 AST，不重建调用目标。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact, ProgramValueTransfer
from backend.app.services.program_graph.contracts import FieldValueOrigin, InstanceFieldEffect
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.value_binding.objects import instance_field_name as field_slot
from backend.app.services.value_binding.access_paths import attribute_path, canonical_path, parent_path
from backend.app.services.value_binding.identities import variable_root

from ..ir import IRLocation, IRValueBoundary
from .call_graph import ProgramArgumentBinder
from .base import ValuePathQuery
from .diagnostics import FlowDiagnostics
from .factory_returns import prepare_factory_returns
from .instance_common import escaped_receivers, instance_result, rebuild as _rebuild, receiver_has_unknown_effect
from .object_contracts import LocalInstance, ObjectReferenceResolver
from .object_references import ObjectReferenceIndex, constructor_reference_fields
from .object_effects import ObjectCallEffects
from .object_types import ObjectTypes
from .constructor_effects import delegate_fields
from .object_storage import prepare_local_storage, mark_unknown_storage_effects
from .collections import collection_instances
from .collection_effects import prepare_collection_effects
from .post_calls import normal_exit_values


def receiver_fields(function: FunctionProgramGraph) -> set[str]:
    """返回已观察到的纯字段路径；组合关系仍须由对象引用环境逐跳确认。"""
    context = function.instance_context
    if context is None:
        return set()
    prefix = context.receiver + "."
    return {name for node in function.nodes.values() for name in (*node.uses, *node.definitions)
            if name.startswith(prefix) and canonical_path(name, selectors=True) is not None
            and canonical_path(name, selectors=True).segments[0].value not in context.shared_fields}


def receiver_members(paths: set[str]) -> set[str]:
    """从已规范化的接收者访问提取直属字段；后续字段和下标仍由引用环境逐跳解析。"""
    return {str(path.segments[0].value) for name in paths
            if (path := canonical_path(name, selectors=True)) is not None and path.segments
            and path.segments[0].kind == "attribute" and str(path.segments[0].value).isidentifier()}


def prepare_instance_fields(
    graph: ProgramGraphArtifact, index: SemanticIndex | None, binder: ProgramArgumentBinder,
    find_path: ValuePathQuery, diagnostics: FlowDiagnostics | None = None,
    *, max_summary_paths: int = 256,
) -> tuple[ProgramGraphArtifact, list[IRValueBoundary]]:
    """输入公共图、语义索引及路径查询，返回私有字段 Overlay 与初始化/工厂返回边界。

    分配身份由调用位置决定；接受唯一解析的局部实例/精确 return 槽位及唯一构造器。
    通过构造器 CFG 的正常出口提取参数到字段的依赖，尊重安全覆盖和不可达写入。
    工厂字段返回复用既有值边界；局部直接引用别名使用公共身份，不污染整对象。
    对象参数与接收者共用正常出口接口；消费已有父初始化调用与类型层次，不推断任意堆或并发。
    """
    if index is None:
        return graph, []
    graph, storage_instances = prepare_local_storage(graph, index, diagnostics)
    for method, values in collection_instances(graph, diagnostics).items():
        # 集合声明不重复作为普通结构体分配；保持同一次语法分配一个身份。
        names = {(item.node_id, item.result_name) for item in values}
        storage_instances[method] = [item for item in storage_instances.get(method, ()) if (item.node_id, item.result_name) not in names] + values
    graph = prepare_collection_effects(graph, storage_instances, diagnostics)
    types = ObjectTypes(graph, index)
    initializer_boundaries: list[IRValueBoundary] = []
    constructors: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
    methods: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
    fields: dict[str, set[str]] = {}
    updated = dict(graph.functions)
    instances: dict[str, list[LocalInstance]] = defaultdict(list, storage_instances)
    active_types = {target.target_symbol for fact in index.artifact.callsites.values()
                    for target in fact.targets if target.relation == "instantiates"}
    active_types.update(item.owner_symbol for items in storage_instances.values() for item in items)
    active_types.update(ancestor for actual in tuple(active_types) for ancestor in types.hierarchy.ancestors(actual))
    for function in graph.functions.values():
        context = function.instance_context
        if context is None or context.owner_symbol not in active_types or not function.has_body:
            continue
        methods[function.symbol_id].append(function)
        if diagnostics is not None and context.shared_fields and any(
            name in context.shared_fields or name in {context.receiver + "." + field for field in context.shared_fields}
            for node in function.nodes.values() for name in (*node.uses, *node.definitions)):
            diagnostics.record("instance_shared_storage_not_modelled", symbol=function.symbol_id,
                               fields=context.shared_fields)
        fields[function.method_id] = receiver_fields(function)
        if context.is_constructor:
            constructors[context.owner_symbol].append(function)
        # 这是未赋值字段的抽象入口，不是输入 Source，也不是实参位置。
        entry = function.nodes[function.entry_node_id]
        nodes = dict(function.nodes)
        if fields[function.method_id]:
            nodes[entry.node_id] = entry.model_copy(update={"definitions": sorted(set(entry.definitions) | fields[function.method_id])})
            updated[function.method_id] = _rebuild(function, nodes)
    owner_fields: dict[str, set[str]] = defaultdict(set)
    for function in graph.functions.values():
        context = function.instance_context
        if context is not None:
            owner_fields[context.owner_symbol].update(receiver_members(fields.get(function.method_id, set())))
    for actual in active_types:
        for ancestor in types.hierarchy.ancestors(actual):
            if ancestor != actual:
                owner_fields[actual].update(owner_fields.get(ancestor, ()))
    summaries: dict[str, list[tuple[str, str, list[FieldValueOrigin]]]] = {}
    reference_summaries: dict[str, dict[str, str]] = {}
    must_writes: dict[str, set[str]] = {}
    rejected: set[str] = set()
    for owner in sorted(constructors, key=lambda owner: len(types.hierarchy.ancestors(owner))):
        candidates = constructors[owner]
        if len(candidates) != 1:
            continue
        for constructor in candidates:
            current = delegate_fields(updated[constructor.method_id], index, binder, types, summaries, must_writes, fields)
            updated[constructor.method_id] = current
            inherited_names = receiver_fields(current)
            fields[constructor.method_id].update(inherited_names)
            # 布局只保存直属成员；child.value 属于逐跳访问，不能装入 ObjectFieldSlot。
            owner_fields[owner].update(receiver_members(inherited_names))
            names = fields[constructor.method_id]
            entry = current.nodes[current.entry_node_id]
            updated[constructor.method_id] = current.model_copy(update={"nodes": {**current.nodes,
                entry.node_id: entry.model_copy(update={"definitions": sorted(set(entry.definitions) | names)})}})
            rebound = any(constructor.instance_context.receiver in node.definitions
                          for node in constructor.nodes.values() if node.node_id != constructor.entry_node_id)
            if any(not name[len(constructor.instance_context.receiver) + 1:].isidentifier() for name in names):
                rejected.add(constructor.method_id)
                if diagnostics is not None:
                    diagnostics.record("instance_nested_reference_not_resolved", symbol=constructor.symbol_id,
                                       reason_detail="constructor_mutates_nested_object")
                continue
            if rebound or len(names) * len(constructor.parameters) > max_summary_paths:
                rejected.add(constructor.method_id)
                if diagnostics is not None:
                    diagnostics.record("instance_receiver_rebound" if rebound else "instance_summary_budget_exceeded", symbol=constructor.symbol_id,
                                       limit=max_summary_paths, fields=len(names), parameters=len(constructor.parameters))
                summaries[constructor.method_id] = []
                continue
            # 与普通调用写回共用正常出口值观察，不让失败构造路径写入调用方对象。
            base, normal_id = normal_exit_values(updated[constructor.method_id], names)
            view = _rebuild(base, base.nodes)
            carried = {edge.variable for edge in view.edges if edge.kind == "reaching_def"
                       and edge.source == view.entry_node_id and edge.target == normal_id}
            must_writes[constructor.method_id] = names - carried if constructor.normal_exit_writes_modelled else set()
            reference_summaries[constructor.method_id] = constructor_reference_fields(view, names, normal_id)
            summary = []
            for parameter in constructor.parameters:
                if parameter == constructor.instance_context.receiver:
                    continue
                for name in sorted(names):
                    path = find_path(view, source_node_id=view.entry_node_id, source_names=(parameter,),
                                     sink_node_id=normal_id, sink_names={name})
                    if path is None:
                        continue
                    origins = [FieldValueOrigin(symbol=constructor.symbol_id, location=view.nodes[edge.target].location,
                                                expression=view.nodes[edge.target].code,
                                                input_names=[edge.source_variable or edge.variable or parameter],
                                                output_names=[edge.target_variable or edge.variable or name])
                               for edge in path[:-1] if view.nodes[edge.target].definitions and view.nodes[edge.target].location]
                    origins.extend(origin for edge in path[:-1] for details in view.nodes[edge.target].object_alias_origins.values()
                                   for origin in details)
                    summary.append((parameter, name, origins))
            summaries[constructor.method_id] = summary
    for actual in active_types:
        for ancestor in types.hierarchy.ancestors(actual):
            owner_fields[actual].update(owner_fields.get(ancestor, ()))
    call_effects = ObjectCallEffects(graph.model_copy(update={"functions": updated}), index, binder, diagnostics,
                                    instances=instances, constructor_references=reference_summaries)
    for caller in graph.functions.values():
        nodes = dict(updated[caller.method_id].nodes)
        effects: list[InstanceFieldEffect] = []
        allocations_by_call: dict[str, str] = {}
        calls = {call.callsite_id: (node, call) for node in caller.nodes.values() for call in node.calls}
        # 直接名称别名由引用环境核实；不能让旧字段摘要跨逃逸或未知方法副作用继续传播。
        prospective = []
        for fact in index.calls_from(caller.symbol_id):
            hit = calls.get(fact.callsite_id)
            if hit is None or len(fact.targets) != 1 or fact.targets[0].relation != "instantiates":
                continue
            candidates = types.initializers(fact.targets[0].target_symbol)
            receiver = instance_result(*hit)
            if len(candidates) != 1 or receiver is None or candidates[0].method_id in rejected:
                continue
            owner = fact.targets[0].target_symbol
            if types.hidden_fields(owner):
                continue
            allocation = "allocation:" + hashlib.sha256((caller.method_id + "\x1f" + fact.callsite_id).encode()).hexdigest()[:24]
            prospective.append(LocalInstance(hit[0].node_id, fact.callsite_id, allocation, owner, receiver, tuple(sorted(owner_fields[owner]))))
        constructor_arguments: set[tuple[str, str]] = set()
        for item in prospective:
            node, call = calls[item.callsite_id]
            constructor = types.initializers(item.owner_symbol)[0]
            actuals = dict(binder.bind_references(call, constructor))
            projections = []
            origins = {}
            for name, parameter in reference_summaries.get(constructor.method_id, {}).items():
                actual = actuals.get(parameter)
                if actual is not None:
                    constructor_arguments.add((call.callsite_id, actual))
                    projections.append(ProgramValueTransfer(output_variable=item.result_name + name[len(constructor.instance_context.receiver):],
                        input_variables=[actual], reference_source=actual, transfer_kind="assignment", provenance="inferred"))
                    origins[projections[-1].output_variable] = [origin for source, field, details in summaries[constructor.method_id]
                        if source == parameter and field == name for origin in details]
            if projections:
                current = nodes[node.node_id]
                nodes[node.node_id] = current.model_copy(update={
                    "definitions": sorted(set(current.definitions) | {transfer.output_variable for transfer in projections}),
                    "value_transfers": [*current.value_transfers, *projections],
                    "object_alias_origins": {**current.object_alias_origins, **origins}})
        preview = caller.model_copy(update={"nodes": nodes})
        preview_instances = [*prospective, *instances.get(caller.method_id, ())]
        preview_references = ObjectReferenceIndex(preview, preview_instances) if preview_instances else None
        allowed_arguments, allowed_calls = call_effects.permissions_for(preview, preview_instances, references=preview_references)
        if instances.get(caller.method_id):
            preview = mark_unknown_storage_effects(preview, preview_references, allowed_arguments | frozenset(constructor_arguments), allowed_calls, diagnostics)
            nodes = dict(preview.nodes)
        rejected_receivers = escaped_receivers(preview, allowed_arguments=allowed_arguments | frozenset(constructor_arguments),
            local_objects=frozenset(item.result_name for item in preview_instances), references=preview_references)
        for fact in index.calls_from(caller.symbol_id):
            allocations = [target for target in fact.targets if target.relation == "instantiates"]
            if not allocations or fact.callsite_id not in calls:
                continue
            node, call = calls[fact.callsite_id]
            candidates = types.initializers(allocations[0].target_symbol) if len(allocations) == 1 else []
            # 进入初始化方法不要求后续对象身份可追踪；工厂/嵌套分配也可检查方法体。
            # 复用既有值边界，不伪造 CALLS，也不把构造器 return 当成分配结果。
            allocation_id = "allocation:" + hashlib.sha256((caller.method_id + "\x1f" + call.callsite_id).encode()).hexdigest()[:24]
            bindings = binder.bind(call, candidates[0]) if len(candidates) == 1 else []
            for parameter, values in bindings:
                constructor = candidates[0]
                initializer_boundaries.append(IRValueBoundary(
                    boundary_id="boundary:" + hashlib.sha256((allocation_id + "\x1f" + constructor.method_id + "\x1f" + parameter).encode()).hexdigest()[:24],
                    kind="instance_initializer_argument", source_scope=IRLocation(**caller.location.model_dump()),
                    source_location=IRLocation(**call.location.model_dump()), source_names=values,
                    target_scope=IRLocation(**constructor.location.model_dump()),
                    target_location=IRLocation(**constructor.location.model_dump()), target_names=(parameter,), capture=True,
                    limitations=("初始化参数绑定只基于唯一静态实例化目标，属于 inferred/may，不证明运行时一定执行；初始化返回值不代表实例内容。",),
                    reference_edge_id=allocations[0].edge_id, callsite_id=call.callsite_id, object_allocation_id=allocation_id,
                    object_type_symbol=constructor.instance_context.owner_symbol))
            receiver = instance_result(node, call)
            if len(candidates) != 1 or receiver is None:
                if diagnostics is not None:
                    diagnostics.record("instance_allocation_not_modelled", symbol=caller.symbol_id,
                                       callsite_id=call.callsite_id, **call.location.model_dump())
                continue
            constructor = candidates[0]
            if constructor.method_id in rejected:
                continue
            hidden = types.hidden_fields(allocations[0].target_symbol)
            if hidden:
                if diagnostics is not None:
                    diagnostics.record("storage_member_not_resolved", symbol=caller.symbol_id, callsite_id=call.callsite_id,
                                       fields=sorted(hidden), reason_detail="hidden_fields_require_declaration_storage_identity")
                continue
            if variable_root(receiver) in rejected_receivers or receiver_has_unknown_effect(preview, index, methods, receiver,
                    {allocations[0].target_symbol}, allowed_calls=allowed_calls, references=preview_references, object_types=types):
                if diagnostics is not None:
                    diagnostics.record("instance_alias_escape_or_mutation_not_modelled", symbol=caller.symbol_id,
                                       receiver=receiver, callsite_id=call.callsite_id, **call.location.model_dump())
                continue
            context = constructor.instance_context
            actual_owner = allocations[0].target_symbol
            instances[caller.method_id].append(LocalInstance(node.node_id, call.callsite_id, allocation_id,
                actual_owner, receiver, tuple(sorted(owner_fields[actual_owner]))))
            allocations_by_call[call.callsite_id] = allocation_id
            argument_values = dict(bindings)
            transfers = [item.model_copy(update={"input_variables": []})
                         if call.callsite_id in item.callsite_ids and item.transfer_kind == "call_result" else item
                         for item in nodes[node.node_id].value_transfers]
            definitions = set(nodes[node.node_id].definitions)
            slots = {slot for name in owner_fields[actual_owner]
                     for slot in (receiver + "." + name, field_slot(receiver, actual_owner, name))}
            definitions.update(slots)
            for parameter, name, origins in summaries[constructor.method_id]:
                values = argument_values.get(parameter, ())
                if not values:
                    continue
                short_name = name[len(context.receiver) + 1:]
                for output in (receiver + "." + short_name, field_slot(receiver, actual_owner, short_name)):
                    if not any(item.output_variable == output and item.input_variables == list(values) for item in transfers):
                        transfers.append(ProgramValueTransfer(output_variable=output, input_variables=list(values),
                                                               transfer_kind="assignment", certainty="may", provenance="inferred"))
                    effects.append(InstanceFieldEffect(allocation_id=allocation_id, node_id=node.node_id,
                                   callsite_id=call.callsite_id, reference_edge_id=allocations[0].edge_id,
                                   constructor_symbol=constructor.symbol_id, parameter=parameter, field_name=name,
                                   output_name=output, input_names=list(values), location=call.location, origins=origins,
                                   object_type_symbol=actual_owner))
            transfers.extend(ProgramValueTransfer(output_variable=slot, input_variables=[], transfer_kind="assignment",
                provenance="inferred") for slot in sorted(slots - {item.output_variable for item in transfers}))
            nodes[node.node_id] = nodes[node.node_id].model_copy(update={"definitions": sorted(definitions), "value_transfers": transfers})
        if nodes != updated[caller.method_id].nodes or allocations_by_call:
            updated[caller.method_id] = _rebuild(updated[caller.method_id], nodes, field_effects=effects, instance_allocations=allocations_by_call,
                limitations=[*caller.limitations, "实例字段及存储仅支持有界局部分配、工厂与已解析初始化/正常出口写回，按 inferred/may 抽象；未建模完整别名、任意堆副作用或并发。"])
    graph, local_writebacks = call_effects.apply(graph.model_copy(update={"functions": updated}), instances)
    graph, return_boundaries = prepare_factory_returns(graph, index, instances,
        methods, diagnostics, max_paths=max_summary_paths, object_effects=call_effects)
    updated = dict(graph.functions)
    # 直接分配和工厂返回共用这一读取接口，不复制一套方法污点算法。
    for caller in graph.functions.values():
        local_instances = instances.get(caller.method_id, ())
        if not local_instances:
            continue
        references: ObjectReferenceResolver = ObjectReferenceIndex(caller, list(local_instances), diagnostics)
        nodes = dict(caller.nodes)
        calls = {call.callsite_id: (node, call) for node in caller.nodes.values() for call in node.calls}
        for fact in index.calls_from(caller.symbol_id):
            hit = calls.get(fact.callsite_id)
            if hit is None:
                continue
            node, call = hit
            if attribute_path(call.receiver) is None:
                continue
            possible = references.possible(node, call.receiver)
            owners = {item.owner_symbol for item in possible}
            receiver_bindings: dict[str, dict[str, str]] = {}
            for target in fact.targets:
                for method in methods.get(target.target_symbol, ()):
                    context = method.instance_context
                    compatible = {owner for owner in owners if types.accepts(owner, method)}
                    if context.is_constructor or not compatible:
                        if not context.is_constructor and fields[method.method_id] and diagnostics is not None:
                            diagnostics.record("instance_receiver_identity_not_modelled", symbol=caller.symbol_id,
                                               receiver=call.receiver, callsite_id=call.callsite_id, **call.location.model_dump())
                        continue
                    mapping = {}
                    for name in fields[method.method_id]:
                        actual = call.receiver + name[len(context.receiver):]
                        slot = references.field_binding(node, actual, write=False)
                        parent = parent_path(actual)
                        leaf_owners = references.possible(node, parent)
                        if slot is not None and len(leaf_owners) == 1:
                            mapping[name] = field_slot(parent, leaf_owners[0].owner_symbol, slot.field_name)
                        elif slot is not None and parent == call.receiver and any(
                                item.owner_symbol == context.owner_symbol for item in leaf_owners):
                            # 同一工厂分配位置的多类型变体仍各用本类型槽，不能合并兄弟值。
                            mapping[name] = field_slot(parent, context.owner_symbol, slot.field_name)
                    receiver_bindings[method.method_id] = mapping
            if possible or call.receiver in {item.result_name for item in local_instances} or references.at(node, call.receiver).alias_nodes:
                current = nodes[node.node_id]
                nodes[node.node_id] = current.model_copy(update={
                    "uses": sorted(set(current.uses) | {value for mapping in receiver_bindings.values() for value in mapping.values()}),
                    "calls": [item.model_copy(update={"receiver_field_bindings": receiver_bindings,
                        "receiver_alias_origins": references.alias_origins(node, call.receiver) if possible else [],
                        "receiver_allocation_id": possible[0].allocation_id if possible else ""})
                        if item.callsite_id == call.callsite_id else item for item in current.calls]})
        updated[caller.method_id] = references.bind_fields(caller.model_copy(update={"nodes": nodes}))
    return graph.model_copy(update={"functions": updated}), [*initializer_boundaries, *local_writebacks, *return_boundaries]
