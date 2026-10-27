"""实例初始化与工厂返回共用的局部绑定和私有图操作。"""

from typing import Any

from backend.app.services.program_graph import FunctionProgramGraph, ProgramCallSite, ProgramGraphNode, ProgramValueTransfer
from backend.app.services.program_graph.passes import ReachingDefinitionsPass, ValueFlowPass
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.value_binding.access_paths import attribute_path, canonical_path, descendant
from backend.app.services.value_binding.identities import variable_root
from .object_contracts import ObjectReferenceResolver
from .object_types import ObjectTypes


def rebuild(function: FunctionProgramGraph, nodes: dict[str, ProgramGraphNode], **updates: Any) -> FunctionProgramGraph:
    """输入私有节点与更新项，复用公共到达定义和值流 Pass，不修改原函数。"""
    return ValueFlowPass().apply(ReachingDefinitionsPass().apply(function.model_copy(update={"nodes": nodes, **updates})))


def instance_result(node: ProgramGraphNode, call: ProgramCallSite) -> str | None:
    """接受单一名称/静态字段接收者或已隔离的 return 槽；实际父对象另由引用环境核实。"""
    if len(node.calls) != 1:
        return None
    if call.result_variable.startswith("call-result:") and node.kind == "return":
        return call.result_variable
    destinations = call.result_targets or node.definitions
    return destinations[0] if len(destinations) == 1 and canonical_path(destinations[0]) is not None else None


def escaped_receivers(function: FunctionProgramGraph, *, allowed_arguments: frozenset[tuple[str, str]] = frozenset(),
                      local_objects: frozenset[str] = frozenset(),
                      references: ObjectReferenceResolver | None = None) -> set[str]:
    """返回不支持的绑定或参数逃逸名字，并保守扩展到别名组；直接 return 另行核实。"""
    aliases = reference_alias_groups(function)
    known = set(local_objects) | set(function.parameters)
    if function.instance_context is not None:
        known.add(function.instance_context.receiver)
    for root in local_objects:
        known.update(aliases.get(root, ()))
    rejected = {variable_root(name) for node in function.nodes.values() for transfer in node.value_transfers
                for name in transfer.input_variables if (name.isidentifier()
                or references is not None and references.possible(node, name))
                and transfer.output_variable != name and transfer.transfer_kind != "call_result"
                and not (is_reference_assignment(function, transfer)
                         and (transfer.output_variable.isidentifier() or variable_root(transfer.output_variable) in known
                              or any(descendant(transfer.output_variable, root) for root in known)))
                and not (node.kind == "return" and node.return_values == [[name]])}
    for node in function.nodes.values():
        observed = {transfer.output_variable for transfer in node.value_transfers if transfer.provenance == "observed"}
        rejected.update(root for root in observed if root.isidentifier()
                        and any(name.startswith(root + ".") for name in observed))
        for call in node.calls:
            arguments = [(names, call.positional_references[index] if index < len(call.positional_references) else "")
                         for index, names in enumerate(call.positional_arguments)]
            arguments.extend((names, call.keyword_references.get(key, "")) for key, names in call.keyword_arguments.items())
            rejected.update(variable_root(name) for names, reference in arguments for name in names
                            if (name.isidentifier() or references is not None and references.possible(node, name))
                            and ((call.callsite_id, name) not in allowed_arguments or reference != name))
    groups = reference_alias_groups(function, include_fields=True, references=references)
    return set().union(rejected, *(groups.get(name, set()) for name in rejected))


def is_reference_assignment(function: FunctionProgramGraph, transfer: ProgramValueTransfer) -> bool:
    """已声明引用语义且 AST 确认的名称/字段复制；旧产物只回退直接名称，不猜表达式。"""
    if transfer.storage_mode in {"reference", "address"}:
        return len(transfer.input_variables) == 1 and transfer.reference_source == transfer.input_variables[0]
    if transfer.storage_mode or function.object_assignment != "reference" or transfer.callsite_ids or len(transfer.input_variables) != 1:
        return False
    source = transfer.input_variables[0]
    if transfer.reference_source:
        output = transfer.output_variable
        private_path = transfer.provenance == "inferred" and output.startswith("call-result:") and "." in output \
            and attribute_path("result" + output[output.index("."):]) is not None
        return (transfer.reference_source == source and canonical_path(source) is not None
                and (canonical_path(output) is not None or private_path))
    return (transfer.transfer_kind == "assignment" and transfer.output_variable.isidentifier() and source.isidentifier())


def reference_alias_groups(function: FunctionProgramGraph, *, include_fields: bool = False,
                           references: ObjectReferenceResolver | None = None) -> dict[str, set[str]]:
    """保守连通组只用于未知副作用拒绝；可包含字段组合，但不用于证明对象身份。"""
    adjacent: dict[str, set[str]] = {}
    for node in function.nodes.values():
        for transfer in node.value_transfers:
            if is_reference_assignment(function, transfer) and (include_fields or
                    transfer.output_variable.isidentifier() and transfer.input_variables[0].isidentifier()):
                if (not transfer.output_variable.isidentifier() or not transfer.input_variables[0].isidentifier()) and (
                        references is None or not references.possible(node, transfer.input_variables[0])):
                    continue
                source, target = variable_root(transfer.input_variables[0]), variable_root(transfer.output_variable)
                if not source or not target:
                    continue  # 私有返回槽不是空名称；不得把所有无接收者调用并成一个别名组。
                adjacent.setdefault(source, set()).add(target)
                adjacent.setdefault(target, set()).add(source)
    result: dict[str, set[str]] = {}
    for name in adjacent:
        if name in result:
            continue
        group, pending = set(), [name]
        while pending:
            current = pending.pop()
            if current not in group:
                group.add(current)
                pending.extend(adjacent[current] - group)
        result.update((member, group) for member in group)
    return result


def receiver_has_unknown_effect(function: FunctionProgramGraph, index: SemanticIndex,
                                methods: dict[str, list[FunctionProgramGraph]], root: str, owners: set[str],
                                *, allowed_calls: frozenset[str] = frozenset(),
                                references: ObjectReferenceResolver | None = None,
                                object_types: ObjectTypes | None = None) -> bool:
    """仅核对可能实际类型的既有方法目标；抽象基类目标不与子类状态混用，不新增派发边。"""
    aliases = reference_alias_groups(function).get(root, {root})
    related = reference_alias_groups(function, include_fields=True, references=references).get(root, {root})
    for fact in index.calls_from(function.symbol_id):
        if fact.receiver not in aliases and variable_root(fact.receiver) in related and fact.callsite_id not in allowed_calls:
            return True
        if fact.receiver not in aliases:
            continue
        if fact.callsite_id in allowed_calls:
            continue
        targets = [(method, context) for target in fact.targets for method in methods.get(target.target_symbol, ())
                   if (context := method.instance_context) is not None and method.has_body and any(
                       object_types.accepts(owner, method) if object_types else context.owner_symbol == owner for owner in owners)]
        covered = {owner for owner in owners if any(object_types.accepts(owner, method) for method, _ in targets)} \
            if object_types else {context.owner_symbol for _, context in targets}
        if fact.truncated or any(target.truncated for target in fact.targets) or owners - covered:
            return True
        if any(name.startswith(context.receiver + ".") for method, context in targets
               for node in method.nodes.values() if node.node_id != method.entry_node_id for name in node.definitions):
            return True
    return False
