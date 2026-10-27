"""公共工厂对象返回：只消费既有调用目标、局部实例和 CFG，不识别语言 AST。"""

from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact
from backend.app.services.program_graph.contracts import ProgramGraphNode
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.semantic_index.contracts import CallSiteFact
from backend.app.services.value_binding.objects import instance_field_name as field_slot
from backend.app.services.value_binding.access_paths import member_path

from ..ir import IRLocation, IRValueBoundary
from .diagnostics import FlowDiagnostics
from .instance_common import escaped_receivers, instance_result, rebuild, receiver_has_unknown_effect
from .object_contracts import LocalInstance, ObjectCallCompiler, ObjectSnapshot
from .object_snapshots import import_snapshot, snapshot_fields
from .object_references import ObjectReferenceIndex
from .object_types import ObjectTypes


@dataclass(frozen=True)
class FactoryReturnSite:
    """真实返回位置及该时刻的可达对象快照；不保存无关堆或调用前的旧状态。"""

    node_id: str
    snapshot: ObjectSnapshot
    source_roots: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FactoryVariant:
    """一种具体返回类型的字段接口；保留其真实 return 节点，不与其他类型合并。"""

    owner_symbol: str
    depth: int
    return_sites: tuple[FactoryReturnSite, ...]


@dataclass(frozen=True)
class FactoryResult:
    """一个工厂的有界返回变体集合；不使用声明的基类代替实际分配类型。"""

    variants: tuple[FactoryVariant, ...]

    @property
    def depth(self) -> int:
        """返回最深组合层数，确保有限迭代包含全部已知变体而非截取部分。"""
        return max(variant.depth for variant in self.variants)


def _export(function: FunctionProgramGraph, instances: list[LocalInstance],
            diagnostics: FlowDiagnostics | None, max_paths: int) -> tuple[FunctionProgramGraph, FactoryResult] | None:
    """在每个可达 return 查询同一引用环境，导出可达内部对象而非只按返回变量名找构造器。"""
    if function.deferred_return:
        if diagnostics is not None:
            diagnostics.record("instance_factory_return_not_modelled", symbol=function.symbol_id, reason_detail="deferred_return")
        return None
    references = ObjectReferenceIndex(function, instances, diagnostics)
    if not references.available:
        return None
    grouped: dict[str, list[FactoryReturnSite]] = defaultdict(list)
    nodes = dict(function.nodes)
    total = 0
    for node in function.nodes.values():
        if node.kind != "return" or node.node_id not in references.before:
            continue
        names = node.return_values[0] if len(node.return_values) == 1 and node.return_value_forms in (["reference"], ["call"]) else []
        name = names[0] if len(names) == 1 else ""
        binding = references.at(node, name, write=name in node.definitions)
        if not name or "" in binding.allocations or not binding.allocations:
            if diagnostics is not None:
                diagnostics.record("instance_factory_return_not_modelled", symbol=function.symbol_id,
                                   reason_detail="unknown_or_overwritten_return", **node.location.model_dump())
            return None
        uses = set(node.uses)
        for allocation in sorted(binding.allocations):
            snapshot = references.snapshot(node, name, allocation_id=allocation, write=name in node.definitions, max_objects=max_paths)
            if snapshot is None:
                return None
            total += len(snapshot_fields(snapshot))
            if total > max_paths:
                if diagnostics is not None:
                    diagnostics.record("instance_factory_return_not_modelled", symbol=function.symbol_id,
                                       reason_detail="return_budget_exceeded", limit=max_paths)
                return None
            root = next(item for item in snapshot.objects if item.allocation_id == snapshot.root_id)
            source_roots = []
            for item in snapshot.objects:
                source = name + item.path
                if references.at(node, source).unique_allocation != item.allocation_id:
                    source = next((variant.result_name for variant in item.variants
                                   if references.at(node, variant.result_name).unique_allocation == item.allocation_id), source)
                source_roots.append((item.allocation_id, source))
            for owner in sorted({item.owner_symbol for item in root.variants}):
                selected = replace(snapshot, objects=tuple(replace(item, variants=tuple(
                    variant for variant in item.variants if variant.owner_symbol == owner)) if item is root else item
                    for item in snapshot.objects))
                grouped[owner].append(FactoryReturnSite(node.node_id, selected, tuple(source_roots)))
            source_names = dict(source_roots)
            uses.update(field_slot(source_names[item.allocation_id], item.owner_symbol, item.field_name) for item in snapshot_fields(snapshot))
        nodes[node.node_id] = node.model_copy(update={"uses": sorted(uses)})
    if not grouped:
        return None
    variants = tuple(FactoryVariant(owner,
                      1 + max(variant.factory_depth for site in sites for item in site.snapshot.objects for variant in item.variants),
                      tuple(sites)) for owner, sites in sorted(grouped.items()))
    return references.bind_fields(function.model_copy(update={"nodes": nodes})), FactoryResult(variants)


def prepare_factory_returns(
    graph: ProgramGraphArtifact, index: SemanticIndex, instances: dict[str, list[LocalInstance]],
    methods: dict[str, list[FunctionProgramGraph]],
    diagnostics: FlowDiagnostics | None = None, *, max_depth: int = 4, max_paths: int = 256,
    object_effects: ObjectCallCompiler | None = None,
) -> tuple[ProgramGraphArtifact, list[IRValueBoundary]]:
    """有界自底向上建立工厂字段返回；返回私有视图和绑定实际调用点的 may 值边界。

    instances 是本次编译私有的局部绑定表，会追加已确认的工厂结果，供共享方法读取层使用。
    全部可达 return 必须由已知分配/工厂解释；按具体类型保留变体，不按返回注解猜测。
    """
    updated = dict(graph.functions)
    types = ObjectTypes(graph, index)
    by_symbol: dict[str, list[str]] = defaultdict(list)
    for function in updated.values():
        by_symbol[function.symbol_id].append(function.method_id)
    callers: dict[str, list[CallSiteFact]] = defaultdict(list)
    for fact in index.artifact.callsites.values():
        if len(fact.targets) == 1 and fact.targets[0].relation == "calls" and not (fact.truncated or fact.targets[0].truncated):
            callers[fact.targets[0].target_symbol].append(fact)
    exported: dict[str, FactoryResult] = {}
    boundaries: list[IRValueBoundary] = []
    for _ in range(max_depth):
        fresh: dict[str, FactoryResult] = {}
        for method_id, bindings in list(instances.items()):
            function = updated[method_id]
            if method_id in exported or not bindings or (function.instance_context and function.instance_context.is_constructor):
                continue
            output = _export(function, bindings, None, max_paths)
            if output is not None:
                view, result = output
                if result.depth <= max_depth:
                    updated[method_id], fresh[method_id] = view, result
        if not fresh:
            break
        exported.update(fresh)
        pending: dict[str, list[CallSiteFact]] = defaultdict(list)
        for method_id in fresh:
            for fact in callers.get(updated[method_id].symbol_id, ()):
                for caller_id in by_symbol.get(fact.caller_symbol, ()):
                    pending[caller_id].append(fact)
        for caller_id, facts in pending.items():
            caller = updated[caller_id]
            calls = {call.callsite_id: (node, call) for node in caller.nodes.values() for call in node.calls}
            nodes = dict(caller.nodes)
            allocation_ids = dict(caller.instance_allocations)
            prepared = []
            for fact in facts:
                target = fact.targets[0]
                candidates = by_symbol.get(target.target_symbol, ()) if target.relation == "calls" else ()
                if len(candidates) != 1 or candidates[0] not in fresh or fact.callsite_id not in calls:
                    continue
                factory = updated[candidates[0]]
                result = fresh[factory.method_id]
                node, call = calls[fact.callsite_id]
                root = instance_result(node, call)
                if root is None:
                    if diagnostics is not None:
                        diagnostics.record("instance_factory_receiver_not_modelled", symbol=caller.symbol_id,
                                           callsite_id=call.callsite_id, **call.location.model_dump())
                    continue
                allocation_id = "allocation:" + hashlib.sha256((caller.method_id + "\x1f" + call.callsite_id).encode()).hexdigest()[:24]
                current, bindings, imports = node, [], []
                for variant in result.variants:
                    for site in variant.return_sites:
                        imported = import_snapshot(site.snapshot, current, root, context_id=call.callsite_id,
                            depth=variant.depth, root_allocation_id=allocation_id)
                        current = imported.node
                        bindings.extend(imported.instances)
                        imports.append((site, imported))
                bindings = list(dict.fromkeys(bindings))
                prepared.append((fact, factory, result, node, call, root, bindings, current, imports))
            prospective = [*instances.get(caller.method_id, ()), *(binding for item in prepared for binding in item[6])]
            preview = caller.model_copy(update={"nodes": {**caller.nodes, **{item[3].node_id: item[7] for item in prepared}}})
            allowed_arguments, allowed_calls = object_effects.permissions_for(preview, prospective) if object_effects else (frozenset(), frozenset())
            references = ObjectReferenceIndex(preview, prospective)
            rejected = escaped_receivers(preview, allowed_arguments=allowed_arguments,
                local_objects=frozenset(item.result_name for item in prospective), references=references)
            for fact, factory, result, node, call, root, bindings, current, imports in prepared:
                if root in rejected or receiver_has_unknown_effect(preview, index, methods, root,
                        {variant.owner_symbol for variant in result.variants}, allowed_calls=allowed_calls, references=references,
                        object_types=types):
                    if diagnostics is not None:
                        diagnostics.record("instance_factory_receiver_not_modelled", symbol=caller.symbol_id,
                                           callsite_id=call.callsite_id, **call.location.model_dump())
                    continue
                target = fact.targets[0]
                allocation_id = next(binding.allocation_id for binding in bindings if binding.result_name == root)
                instances.setdefault(caller.method_id, []).extend(bindings)
                allocation_ids[call.callsite_id] = allocation_id
                nodes[node.node_id] = current
                for site, imported in imports:
                    identities = dict(imported.allocations)
                    source_names = dict(site.source_roots)
                    for field in imported.fields:
                        source = field_slot(source_names[field.allocation_id], field.owner_symbol, field.field_name)
                        destination = root + field.path
                        boundaries.append(IRValueBoundary(
                            boundary_id="boundary:" + hashlib.sha256((allocation_id + "\x1f" + site.node_id + "\x1f" + source).encode()).hexdigest()[:24],
                            kind="instance_factory_return", source_scope=IRLocation(**factory.location.model_dump()),
                            source_location=IRLocation(**factory.nodes[site.node_id].location.model_dump()),
                            source_names=(source,), source_reads_input=False,
                            target_scope=IRLocation(**caller.location.model_dump()), target_location=IRLocation(**call.location.model_dump()),
                            target_names=tuple(dict.fromkeys((field_slot(destination, field.owner_symbol, field.field_name), member_path(destination, field.field_name)))),
                            reference_edge_id=target.edge_id, callsite_id=call.callsite_id, object_type_symbol=field.owner_symbol,
                            object_allocation_id=identities[field.allocation_id], resume_callsite_id=call.callsite_id,
                            limitations=("工厂返回导出有界可达对象关系并隔离调用；共享子对象不深拷贝，仍为 inferred/may，不证明路径可行。",)))
            if nodes != caller.nodes:
                updated[caller.method_id] = rebuild(caller, nodes, instance_allocations=allocation_ids)
        if object_effects is not None:
            view, writebacks = object_effects.apply(graph.model_copy(update={"functions": updated}), instances)
            updated = dict(view.functions)
            boundaries.extend(writebacks)
    if diagnostics is not None:
        for method_id, bindings in instances.items():
            function = updated[method_id]
            if method_id not in exported and not (function.instance_context and function.instance_context.is_constructor):
                output = _export(function, bindings, diagnostics, max_paths)
                if output is not None and output[1].depth > max_depth:
                    diagnostics.record("instance_factory_depth_limit", symbol=function.symbol_id, limit=max_depth)
    return graph.model_copy(update={"functions": updated}), boundaries
