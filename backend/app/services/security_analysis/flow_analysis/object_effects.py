"""对象字段调用副作用：参数和接收者共享正常出口接口，不解释安全规则或重建调用目标。"""

from collections import defaultdict
from dataclasses import dataclass
import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite, ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.value_binding.objects import CollectionSummarySlot, StorageValueSlot, storage_slot, instance_field_name
from backend.app.services.value_binding.access_paths import canonical_path, descendant, member_path, parent_path

from ..ir import IRLocation, IRValueBoundary
from .call_graph import ProgramArgumentBinder
from .diagnostics import FlowDiagnostics
from .instance_common import instance_result, is_reference_assignment, rebuild
from .object_contracts import LocalInstance, ObjectReferenceResolver, ObjectRootEffect, ObjectReferenceWrite
from .object_references import ObjectReferenceIndex
from .object_reference_effects import ReferenceInterface, reference_interface
from .object_snapshots import import_snapshot, snapshot_fields
from .object_types import ObjectTypes
from .post_calls import append_post_operations, normal_exit_values


@dataclass(frozen=True)
class _ReferenceWrite:
    """一个已验证目的槽及实际对象来源；快照只用于被调函数新建的对象，不克隆外部引用。"""

    effect: ObjectReferenceWrite
    field_name: str
    allocations: tuple[str, ...] = ()
    external_allocations: tuple[tuple[str, str], ...] = ()
    origins: tuple[FieldValueOrigin, ...] = ()


@dataclass(frozen=True)
class _RootBinding:
    """一个已有调用点的对象根配对，不把标量实参提升成对象字段或新调用边。"""

    effect: ObjectRootEffect
    actual: str
    instance: LocalInstance
    slots: tuple[tuple[str, StorageValueSlot, str, str], ...]
    writeback: bool = True
    references: tuple[_ReferenceWrite, ...] = ()


@dataclass(frozen=True)
class _CallPlan:
    """已核对唯一调用目标、局部分配和无别名冲突的调用写回计划。"""

    node: ProgramGraphNode
    call: ProgramCallSite
    target: FunctionProgramGraph
    edge_id: str
    roots: tuple[_RootBinding, ...]
    return_binding: tuple[str, str, str] | None = None  # 接收槽、实际来源、现有分配，不生成新堆对象。


class ObjectCallEffects:
    """单次分析私有的有界对象调用接口；复用原 RD/DFG 和跨函数值边界，不另建污点算法。"""

    def __init__(self, graph: ProgramGraphArtifact, index: SemanticIndex, binder: ProgramArgumentBinder,
                 diagnostics: FlowDiagnostics | None = None, *, max_fields: int = 256,
                 instances: dict[str, list[LocalInstance]] | None = None,
                 constructor_references: dict[str, dict[str, str]] | None = None) -> None:
        """保存已有图/调用索引和绑定器；按需缓存每个函数/对象根的字段接口，拒绝未知副作用。"""
        self.index, self.binder, self.diagnostics = index, binder, diagnostics
        self.types = ObjectTypes(graph, index)
        self.max_fields = max_fields
        self.instances = instances if instances is not None else {}
        self.constructor_references = constructor_references or {}
        self.functions: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
        self.nested_owners: set[str] = set()
        for function in graph.functions.values():
            self.functions[function.symbol_id].append(function)
            parts = function.symbol_id.split("::")
            self.nested_owners.update("::".join(parts[:length]) for length in range(1, len(parts)))
        self.summaries: dict[tuple[str, str], ObjectRootEffect | None] = {}
        self.reference_summaries: dict[str, ReferenceInterface] = {}

    def summary(self, function: FunctionProgramGraph, root: str) -> ObjectRootEffect | None:
        """输入真实函数和对象形参/接收者，返回正常出口的字段接口；不支持时显式诊断并返回 None。"""
        key = (function.method_id, root)
        if key in self.summaries:
            return self.summaries[key]
        self.summaries[key] = None
        context = function.instance_context
        valid_roots = set(function.parameters) | ({context.receiver} if context else set())
        local = self.instances.get(function.method_id, ())
        names = {name for node in function.nodes.values() if not node.node_id.startswith("normal-values:")
                 for name in (*node.uses, *node.definitions) if descendant(name, root)
                 and not any(node.node_id == item.node_id and descendant(name, item.result_name) for item in local)}
        paths_valid = all(canonical_path(name, selectors=True) is not None or root in function.storage_parameters
                          and all(part.isidentifier() or part == "*" for part in name.split(".")) for name in names)
        implicit_receiver = context is not None and context.receiver == root
        pure_stores = self._constructor_stores(function)
        invalid = (not function.has_body or function.object_assignment != "reference" and root not in function.storage_parameters and not implicit_receiver
            or function.deferred_return or root not in valid_roots
            or function.symbol_id in self.nested_owners
            or not paths_valid
            or bool(context and names & {context.receiver + "." + name for name in context.shared_fields})
            or any(root in node.definitions for node in function.nodes.values() if node.node_id != function.entry_node_id)
            or any(root in transfer.input_variables and not (
                       is_reference_assignment(function, transfer) and canonical_path(transfer.output_variable) is not None
                       and canonical_path(transfer.output_variable).root in valid_roots
                       or transfer.provenance == "inferred" and any((call.callsite_id, root) in pure_stores for call in node.calls)
                       and any(node.node_id == item.node_id and transfer.output_variable.startswith(item.result_name + ".") for item in local))
                   for node in function.nodes.values() for transfer in node.value_transfers)
            or any(call.receiver == root or (call.callsite_id, root) not in pure_stores
                   and any(root in values for values in (*call.positional_arguments, *call.keyword_arguments.values()))
                   for node in function.nodes.values() for call in node.calls))
        if invalid or len(names) > self.max_fields:
            if self.diagnostics is not None and names:
                self.diagnostics.record("object_call_effect_budget_exceeded" if len(names) > self.max_fields else "object_call_effect_not_modelled",
                    symbol=function.symbol_id, receiver=root,
                    reason_detail="field_budget" if len(names) > self.max_fields else "rebound_escape_nested_or_unknown_effect")
            return None
        fields = frozenset(name[len(root):].removeprefix(".") for name in names)
        written = frozenset(name[len(root):].removeprefix(".") for node in function.nodes.values() if node.node_id != function.entry_node_id
                            for name in node.definitions if name in names)
        view = function
        nodes = dict(view.nodes)
        entry = nodes[view.entry_node_id]
        nodes[entry.node_id] = entry.model_copy(update={"definitions": sorted(set(entry.definitions) | names)})
        view, normal_id = normal_exit_values(view.model_copy(update={"nodes": nodes}), names)
        view = rebuild(view, view.nodes)
        if not any(edge.kind == "cfg" and edge.target == normal_id for edge in view.edges):
            return None
        carried = {edge.variable for edge in view.edges if edge.kind == "reaching_def"
                   and edge.source == view.entry_node_id and edge.target == normal_id}
        must = frozenset(name for name in written if member_path(root, name) not in carried) if function.normal_exit_writes_modelled else frozenset()
        if function.method_id not in self.reference_summaries:
            self.reference_summaries[function.method_id] = reference_interface(function,
                self.instances.get(function.method_id, []), max_fields=self.max_fields)
        updates = tuple(item for item in self.reference_summaries[function.method_id].writes if descendant(item.target_name, root))
        effect = ObjectRootEffect(root, fields, written, must, normal_id, updates)
        self.summaries[key] = effect
        return effect

    def _constructor_stores(self, function: FunctionProgramGraph) -> set[tuple[str, str]]:
        """复用已核实的构造器纯引用存储摘要，不能把任意 new 调用当作无副作用。"""
        calls = {call.callsite_id: call for node in function.nodes.values() for call in node.calls}
        result = set()
        for fact in self.index.calls_from(function.symbol_id):
            if fact.truncated or len(fact.targets) != 1 or fact.targets[0].relation != "instantiates" or fact.callsite_id not in calls:
                continue
            targets = self.types.initializers(fact.targets[0].target_symbol)
            if len(targets) != 1:
                continue
            parameters = set(self.constructor_references.get(targets[0].method_id, {}).values())
            result.update((fact.callsite_id, actual) for parameter, actual in self.binder.bind_references(calls[fact.callsite_id], targets[0])
                          if parameter in parameters)
        return result

    def plans(self, caller: FunctionProgramGraph, references: ObjectReferenceResolver) -> list[_CallPlan]:
        """仅接受独立调用、唯一项目目标、唯一具体分配/类型和互不别名的对象根；不猜表达式对象。"""
        calls = {call.callsite_id: (node, call) for node in caller.nodes.values() for call in node.calls}
        result = []
        for fact in self.index.calls_from(caller.symbol_id):
            hit = calls.get(fact.callsite_id)
            if hit is None or fact.truncated:
                continue
            possible_receiver = references.possible(hit[0], hit[1].receiver)
            resolutions = [item for item in fact.targets if not possible_receiver or any(
                self.types.accepts(instance.owner_symbol, method) for instance in possible_receiver
                for method in self.functions.get(item.target_symbol, ()))]
            if len(resolutions) != 1:
                continue
            resolved = resolutions[0]
            targets = self.functions.get(resolved.target_symbol, ()) if resolved.relation == "calls" and not resolved.truncated else ()
            if len(targets) != 1:
                continue
            node, call = hit
            if call.object_effect_applied:
                continue
            target = targets[0]
            if len(node.calls) != 1 or node.kind not in {"call", "operation", "assignment"}:
                continue
            pairs = self.binder.bind_references(call, target)
            addressed = {call.positional_references[position] for position in call.addressed_argument_positions
                         if position < len(call.positional_references)}
            context = target.instance_context
            if context is not None and not context.is_constructor and canonical_path(call.receiver) is not None:
                pairs.append((context.receiver, call.receiver))
            actuals = dict(pairs)
            bindings = []
            failed = False
            for parameter, actual in pairs:
                possible = references.possible(node, actual, address=actual in addressed)
                if not possible:
                    continue
                effect = self.summary(target, parameter)
                if (len(possible) != 1 or effect is None
                        or context is not None and parameter == context.receiver and not self.types.accepts(possible[0].owner_symbol, target)):
                    failed = True
                    break
                slots = []
                reference_updates = []
                # 形参字段之间的直接引用可由新接口写回；另存局部别名后间接修改仍须独立建模。
                if any(is_reference_assignment(target, transfer) and descendant(transfer.input_variables[0], parameter)
                       and references.possible(node, actual + transfer.input_variables[0][len(parameter):])
                       and not (canonical_path(transfer.output_variable) is not None and canonical_path(transfer.output_variable).segments and canonical_path(transfer.output_variable).root
                                in set(target.parameters) | ({context.receiver} if context is not None else set()))
                       for operation in target.nodes.values() for transfer in operation.value_transfers):
                    failed = True
                    break
                for name in sorted(effect.fields):
                    suffix = name.replace("*", "__value__") if possible[0].storage_kind == "scalar" else name
                    full = member_path(actual, suffix)
                    slot = references.field_binding(node, full, write=False)
                    parent = parent_path(full)
                    owners = references.possible(node, parent, address=actual in addressed and parent == actual)
                    update = next((item for item in effect.reference_writes if item.target_name == member_path(parameter, name)), None)
                    source_name = ""
                    source_allocations = ()
                    if update is not None and update.source_name:
                        source_path = canonical_path(update.source_name)
                        source_name = actuals.get(source_path.root, "") + update.source_name[len(source_path.root):] if source_path else ""
                        source_allocations = tuple(sorted(references.at(node, source_name).allocations)) if source_name else ()
                    known_source = source_allocations and "" not in source_allocations
                    reference_write = update is not None and (references.possible(node, full)
                        or update.snapshot is not None or known_source)
                    if slot is None or len(owners) != 1 or (name in effect.written_fields and (
                            references.possible(node, full) and not reference_write
                            or any(descendant(other, name) for other in effect.fields))):
                        failed = True
                        break
                    slots.append((name, slot, owners[0].owner_symbol, full))
                    if reference_write:
                        external = []
                        origins = list(references.alias_origins(node, source_name)) if known_source else []
                        for identity, path in update.snapshot.external_paths if update.snapshot is not None else ():
                            parsed = canonical_path(path)
                            source_actual = actuals.get(parsed.root, "") + path[len(parsed.root):] if parsed and parsed.root in actuals else ""
                            allocated = references.at(node, source_actual).unique_allocation if source_actual else None
                            if allocated is not None:
                                external.append((identity, allocated))
                                origins.extend(references.alias_origins(node, source_actual))
                        reference_updates.append(_ReferenceWrite(update, name, source_allocations if known_source else ("",), tuple(external), tuple(origins)))
                bindings.append(_RootBinding(effect, actual, possible[0], tuple(slots),
                    target.storage_parameters.get(parameter, "reference") == "reference", tuple(reference_updates)))
            allocations = [{binding.instance.allocation_id, *(slot.allocation_id for _, slot, _, _ in binding.slots)} for binding in bindings]
            if any(allocations[left] & allocations[right] for left in range(len(allocations)) for right in range(left)):
                failed = True
                if self.diagnostics is not None:
                    self.diagnostics.record("object_call_effect_not_modelled", symbol=caller.symbol_id,
                        callsite_id=call.callsite_id, reason_detail="aliased_object_parameters")
            if sum(len(binding.effect.fields) for binding in bindings) > self.max_fields:
                failed = True
                if self.diagnostics is not None:
                    self.diagnostics.record("object_call_effect_budget_exceeded", symbol=caller.symbol_id,
                        callsite_id=call.callsite_id, limit=self.max_fields)
            if bindings and not failed:
                returned = None
                interface = self.reference_summaries[target.method_id]
                source = canonical_path(interface.return_source)
                output = instance_result(node, call)
                if source and output and source.root in actuals and target.storage_parameters.get(source.root, "reference") == "reference":
                    actual = actuals[source.root] + interface.return_source[len(source.root):]
                    allocated = references.at(node, actual).unique_allocation
                    # 返回深层引用之前若重绑定了其前缀，不能拿调用前的旧子对象作新返回。
                    changed = any(interface.return_source == update.target_name or descendant(interface.return_source, update.target_name)
                                  for update in interface.writes)
                    if allocated and not changed:
                        returned = (output, actual, allocated)
                result.append(_CallPlan(node, call, target, resolved.edge_id, tuple(bindings), returned))
        return result

    def permissions_for(self, caller: FunctionProgramGraph, instances: list[LocalInstance], *,
                        references: ObjectReferenceResolver | None = None) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
        """初始化和工厂结果复用同一前置验证；只开放完整计划，未知调用仍按逃逸/副作用拒绝。"""
        if not instances:
            return frozenset(), frozenset()
        arguments, receivers = self.permissions(self.plans(caller, references or ObjectReferenceIndex(caller, instances)))
        calls = [call for node in caller.nodes.values() for call in node.calls if call.object_effect_applied or call.collection_effect_model]
        return arguments | frozenset((call.callsite_id, name) for call in calls
            for name in (call.object_effect_arguments if not call.collection_effect_model else
                         [name for values in call.positional_arguments for name in values])), \
            receivers | frozenset(call.callsite_id for call in calls if call.object_effect_receiver or call.collection_effect_model)

    @staticmethod
    def permissions(plans: list[_CallPlan]) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
        """只为已验证完整计划开放对象实参/接收者操作，不将一般已解析调用一概视为无副作用。"""
        arguments = frozenset((plan.call.callsite_id, binding.actual) for plan in plans for binding in plan.roots
                              if plan.target.instance_context is None or binding.effect.root != plan.target.instance_context.receiver)
        receivers = frozenset(plan.call.callsite_id for plan in plans if plan.target.instance_context is not None
                              and any(binding.effect.root == plan.target.instance_context.receiver for binding in plan.roots))
        return arguments, receivers

    def apply(self, graph: ProgramGraphArtifact, instances: dict[str, list[LocalInstance]]) -> tuple[ProgramGraphArtifact, list[IRValueBoundary]]:
        """有界逐步编译；前一次引用写回先进入环境，再绑定后续调用，避免按旧拓扑预编译所有调用。"""
        boundaries = []
        for _ in range(64):
            updated, added = self._apply_round(graph, instances)
            boundaries.extend(added)
            if updated is graph:
                return graph, boundaries
            graph = updated
        if self.diagnostics is not None:
            self.diagnostics.record("object_call_effect_budget_exceeded", reason_detail="call_effect_round_limit", limit=64)
        functions = dict(graph.functions)
        for function in graph.functions.values():
            remaining = {fact.callsite_id for fact in self.index.calls_from(function.symbol_id)
                         if any(target.relation == "calls" and target.target_symbol in self.functions for target in fact.targets)}
            nodes = {node.node_id: node.model_copy(update={"storage_invalidations": ["*"]})
                     if any(call.callsite_id in remaining and not call.object_effect_applied for call in node.calls) else node
                     for node in function.nodes.values()}
            if nodes != function.nodes:
                functions[function.method_id] = function.model_copy(update={"nodes": nodes,
                    "limitations": [*function.limitations, "对象调用效果超过 64 轮；剩余项目调用后失效已知引用，不沿用旧堆状态证明路径。"]})
        return graph.model_copy(update={"functions": functions}), boundaries

    def _apply_round(self, graph: ProgramGraphArtifact, instances: dict[str, list[LocalInstance]]) -> tuple[ProgramGraphArtifact, list[IRValueBoundary]]:
        """为已接受实例编译字段入口、正常退出值和调用后写回；复用已有参数绑定和返回型值边界。"""
        self.functions.clear()
        for function in graph.functions.values():
            self.functions[function.symbol_id].append(function)
        self.summaries.clear()
        self.reference_summaries.clear()
        plans = []
        for function in graph.functions.values():
            if not instances.get(function.method_id):
                continue
            pending_calls = {call.callsite_id for node in function.nodes.values() for call in node.calls if not call.object_effect_applied}
            if not any(fact.callsite_id in pending_calls and any(target.relation == "calls" and target.target_symbol in self.functions
                       for target in fact.targets) for fact in self.index.calls_from(function.symbol_id)):
                continue
            candidates = self.plans(function, ObjectReferenceIndex(function, instances[function.method_id]))
            if candidates:
                plans.append(min(candidates, key=lambda plan: (plan.call.location.line, plan.call.location.column, plan.call.callsite_id)))
        if not plans:
            return graph, []
        updated = dict(graph.functions)
        used: dict[str, dict[str, ObjectRootEffect]] = defaultdict(dict)
        for plan in plans:
            used[plan.target.method_id].update((binding.effect.root, binding.effect) for binding in plan.roots)
        for method_id, effects in used.items():
            # 只保存有界字段接口，不缓存或覆盖整份旧函数图，也不投影未实际接入的参数。
            current = updated[method_id]
            nodes = dict(current.nodes)
            entry = nodes[current.entry_node_id]
            entry_names = {member_path(effect.root, field) for effect in effects.values() for field in effect.fields}
            normal_names = set(entry_names)
            for effect in effects.values():
                for update in effect.reference_writes:
                    if update.snapshot is not None:
                        origin = next(item for item in update.snapshot.objects if item.allocation_id == update.snapshot.root_id).variants[0].result_name
                        normal_names.update(instance_field_name(origin + field.path, field.owner_symbol, field.field_name)
                                            for field in snapshot_fields(update.snapshot))
            nodes[entry.node_id] = entry.model_copy(update={"definitions": sorted(set(entry.definitions) | entry_names)})
            current, _ = normal_exit_values(current.model_copy(update={"nodes": nodes}), normal_names)
            updated[method_id] = rebuild(current, current.nodes)
        boundaries = []
        operations: dict[str, dict[str, list[ProgramGraphNode]]] = defaultdict(lambda: defaultdict(list))
        for plan in plans:
            caller = updated[plan.node.method_id]
            node = caller.nodes[plan.node.node_id]
            argument_fields = dict(plan.call.argument_field_bindings)
            mapping = dict(argument_fields.get(plan.target.method_id, {}))
            uses = set(node.uses)
            references = None
            for binding in plan.roots:
                effect, actual = binding.effect, binding.actual
                slots = {name: (slot, owner, full) for name, slot, owner, full in binding.slots}
                for name in sorted(effect.fields):
                    slot, owner, full = slots[name]
                    parent = parent_path(full)
                    actual_field = instance_field_name(parent, owner, slot.field_name)
                    uses.add(actual_field)
                    if plan.target.instance_context is None or effect.root != plan.target.instance_context.receiver:
                        mapping[member_path(effect.root, name)] = actual_field
                for name in sorted(effect.written_fields) if binding.writeback else ():
                    slot, owner, output = slots[name]
                    key = hashlib.sha256((plan.call.callsite_id + "\x1f" + effect.root + "\x1f" + name).encode()).hexdigest()[:24]
                    post = ProgramGraphNode(node_id="object-writeback:" + key, kind="operation", language=caller.language,
                        method_id=caller.method_id, location=plan.call.location, code="object_field_writeback (generated)",
                        definitions=[output], definition_object_slots={output: slot},
                        certainty="must" if name in effect.must_write else "may", provenance="inferred")
                    outputs = (output,)
                    if isinstance(slot, CollectionSummarySlot):
                        references = references or ObjectReferenceIndex(caller, instances.get(caller.method_id, []))
                        members = sorted({field for variant in references.instances.get(slot.allocation_id, ()) for field in variant.fields
                                          if field != "[?]"})
                        aliases = {member_path(parent_path(output), field): storage_slot(slot.allocation_id, field) for field in members}
                        outputs = (output, *aliases)
                        post = post.model_copy(update={"definitions": list(outputs),
                            "definition_object_slots": {output: slot, **aliases}, "certainty": "may"})
                    reference = next((item for item in binding.references if item.field_name == name), None)
                    if reference is not None:
                        post = post.model_copy(update={"object_reference_bindings": {output: list(reference.allocations)},
                            "object_alias_origins": {output: [*reference.origins, *[origin.model_copy(update={"reference_edge_id": plan.edge_id,
                                "callsite_id": plan.call.callsite_id, "object_type_symbol": owner}) for origin in reference.effect.origins]]}})
                        snapshot = reference.effect.snapshot
                        if snapshot is not None:
                            imported = import_snapshot(snapshot, post, output, context_id=plan.call.callsite_id + ":" + name,
                                external_allocations=reference.external_allocations)
                            post = imported.node
                            identities = dict(imported.allocations)
                            post = post.model_copy(update={"object_reference_bindings": {
                                **post.object_reference_bindings, output: [identities[snapshot.root_id]]}})
                            instances.setdefault(caller.method_id, []).extend(imported.instances)
                            origin = next(item for item in snapshot.objects if item.allocation_id == snapshot.root_id).variants[0].result_name
                            for field in imported.fields:
                                parent = output + field.path
                                source = instance_field_name(origin + field.path, field.owner_symbol, field.field_name)
                                destination = instance_field_name(parent, field.owner_symbol, field.field_name)
                                boundary_key = hashlib.sha256((key + "\x1f" + source).encode()).hexdigest()[:24]
                                boundaries.append(IRValueBoundary(boundary_id="boundary:reference-object:" + boundary_key,
                                    kind="object_reference_new_value", source_scope=IRLocation(**plan.target.location.model_dump()),
                                    source_location=IRLocation(**plan.target.location.model_dump()), source_names=(source,),
                                    target_scope=IRLocation(**caller.location.model_dump()), target_location=IRLocation(**plan.call.location.model_dump()),
                                    target_names=tuple(dict.fromkeys((destination, member_path(parent, field.field_name)))), source_node_id=effect.normal_exit_id,
                                    target_node_id=post.node_id, resume_callsite_id=plan.call.callsite_id,
                                    reference_edge_id=plan.edge_id, callsite_id=plan.call.callsite_id,
                                    object_allocation_id=identities[field.allocation_id], object_type_symbol=field.owner_symbol,
                                    limitations=("组合引用写回导出正常出口新对象值；仍为 inferred/may，不证明完整堆或异常路径可行。",)))
                        elif reference.allocations == ("",) and self.diagnostics is not None:
                            self.diagnostics.record("object_reference_effect_not_resolved", symbol=caller.symbol_id,
                                callsite_id=plan.call.callsite_id, value=output, reason_detail="unknown_or_conditional_reference")
                        operations[caller.method_id][node.node_id].append(post)
                        continue
                    operations[caller.method_id][node.node_id].append(post)
                    boundaries.append(IRValueBoundary(boundary_id="boundary:object-writeback:" + key,
                        kind="object_field_writeback", source_scope=IRLocation(**plan.target.location.model_dump()),
                        source_location=IRLocation(**plan.target.location.model_dump()), source_names=(member_path(effect.root, name),),
                        target_scope=IRLocation(**caller.location.model_dump()), target_location=IRLocation(**plan.call.location.model_dump()),
                        target_names=outputs, source_node_id=effect.normal_exit_id, target_node_id=post.node_id,
                        resume_callsite_id=plan.call.callsite_id, reference_edge_id=plan.edge_id, callsite_id=plan.call.callsite_id,
                        object_allocation_id=slot.allocation_id, object_type_symbol=owner,
                        limitations=("字段写回沿唯一已知对象逐跳绑定正常出口；不证明运行路径可行、净化成立或完整堆副作用。",)))
            if mapping:
                argument_fields[plan.target.method_id] = mapping
            if plan.return_binding is not None:
                output, actual, allocation = plan.return_binding
                key = hashlib.sha256((plan.call.callsite_id + "\x1fborrowed-reference-return").encode()).hexdigest()[:24]
                origins = self.reference_summaries[plan.target.method_id].return_origins
                operations[caller.method_id][node.node_id].append(ProgramGraphNode(
                    node_id="object-return-alias:" + key, kind="operation", language=caller.language,
                    method_id=caller.method_id, location=plan.call.location, code="borrowed_reference_return (generated)",
                    definitions=[output], object_reference_bindings={output: [allocation]},
                    object_alias_origins={output: [origin.model_copy(update={"reference_edge_id": plan.edge_id,
                        "callsite_id": plan.call.callsite_id}) for origin in origins]}, provenance="inferred"))
            nodes = dict(caller.nodes)
            allowed_arguments, allowed_receivers = self.permissions([plan])
            nodes[node.node_id] = node.model_copy(update={"uses": sorted(uses), "calls": [call.model_copy(update={
                "argument_field_bindings": argument_fields, "object_effect_applied": True,
                "object_effect_arguments": sorted(name for _, name in allowed_arguments),
                "object_effect_receiver": plan.call.callsite_id in allowed_receivers})
                if call.callsite_id == plan.call.callsite_id else call for call in node.calls]})
            updated[caller.method_id] = caller.model_copy(update={"nodes": nodes})
        for method_id, additions in operations.items():
            view = append_post_operations(updated[method_id], additions)
            updated[method_id] = rebuild(view, view.nodes)
        return graph.model_copy(update={"functions": updated}), boundaries
