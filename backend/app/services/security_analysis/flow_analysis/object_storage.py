"""公共局部存储准备：语法事实→结构体/标量存储身份，实际引用和复制仍由 ObjectReferenceIndex 求解。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact, ProgramValueTransfer
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.semantic_index.hierarchy import TypeHierarchy

from .diagnostics import FlowDiagnostics
from .object_contracts import LocalInstance, ObjectReferenceResolver
from .call_effects import CallOutputEffects


def storage_type(literal: str, path: str, hierarchy: TypeHierarchy) -> str | None:
    """将直接声明类型绑定到唯一项目类型；歧义、数组、函数指针、动态类型不猜测。"""
    if any(marker in literal for marker in ("[", "]", "(", ")", "<")):
        return None
    name = literal.replace("*", "").replace("&", "").strip()
    for prefix in ("const ", "volatile ", "struct ", "class "):
        name = name.removeprefix(prefix).strip()
    if name in hierarchy.types:
        return name
    suffix = name.replace("::", ".").split(".")[-1]
    local = [key for key, fact in hierarchy.types.items() if key.startswith(path + "::") and key.split("::")[-1] == suffix]
    candidates = local or [key for key in hierarchy.types if key.split("::")[-1] == suffix]
    return candidates[0] if len(candidates) == 1 else None


def prepare_local_storage(graph: ProgramGraphArtifact, index: SemanticIndex, diagnostics: FlowDiagnostics | None = None
                          ) -> tuple[ProgramGraphArtifact, dict[str, list[LocalInstance]]]:
    """建立独立值存储和内嵌子对象；堆字面量使用分配身份，指针变量本身不当作受污内容。

    只物化已知布局，标量仅在源码出现取地址时建存储槽；无布局不是安全证明。
    每函数最多 256 个存储对象、内嵌深度 16；不执行项目、不调用编译器或安全规则。
    """
    hierarchy = TypeHierarchy(index.artifact.types)
    instances: dict[str, list[LocalInstance]] = defaultdict(list)
    updated = dict(graph.functions)
    for function in graph.functions.values():
        if function.language not in {"go", "c", "cpp"}:
            continue
        addressed = {transfer.reference_source for node in function.nodes.values() for transfer in node.value_transfers
                     if transfer.storage_mode == "address"}
        addressed.update(call.positional_references[position] for node in function.nodes.values() for call in node.calls
                         for position in call.addressed_argument_positions if position < len(call.positional_references))
        nodes = dict(function.nodes)
        for node in function.nodes.values():
            pending = [(name, allocation.type_literal, allocation.kind, 0) for name, allocation in node.storage_allocations.items()]
            definitions, transfers = set(node.definitions), list(node.value_transfers)
            while pending:
                name, raw, kind, depth = pending.pop(0)
                owner = storage_type(raw, function.location.path, hierarchy) if kind != "scalar_cell" else None
                scalar = owner is None
                if scalar and (name not in addressed or any(mark in raw for mark in "[]()")):
                    continue
                base = raw.replace("*", "").replace("&", "").replace("const ", "").strip()
                if scalar and kind != "scalar_cell" and base not in {
                    "char", "int", "float", "double", "bool", "short", "long", "size_t", "string", "rune", "byte",
                    "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr", "float32", "float64",
                }:
                    continue  # 没有项目布局的自定义类型不是标量；旧/无索引调用方保持原有保守行为。
                if depth > 16 or len(instances[function.method_id]) >= 256:
                    if diagnostics is not None:
                        diagnostics.record("storage_layout_budget_exceeded", symbol=function.symbol_id, node_id=node.node_id, limit=256)
                    break
                owner = owner or "scalar:" + raw
                layout = {}
                if not scalar:
                    ancestors = hierarchy.ancestors(owner, include_embeddings=True)
                    for candidate in ancestors:
                        if function.language == "go" and candidate != owner:
                            continue
                        fact = hierarchy.types.get(candidate)
                        for field, literal in (fact.fields.items() if fact is not None else ()):
                            declaring = {base for base in ancestors if field in hierarchy.types[base].fields}
                            owners = hierarchy.member_owners(owner, declaring)
                            if len(owners) == 1 and owners[0] == candidate:
                                layout[field] = literal
                            elif diagnostics is not None:
                                diagnostics.record("storage_member_not_resolved", symbol=function.symbol_id, member=field,
                                                   reason_detail="ambiguous_inherited_storage")
                allocation = "storage:" + hashlib.sha256((function.method_id + "\x1f" + node.node_id + "\x1f" + name).encode()).hexdigest()[:24]
                embedded = tuple(field for field, literal in layout.items() if "*" not in literal and "&" not in literal
                                 and storage_type(literal, function.location.path, hierarchy) is not None)
                instance = LocalInstance(node.node_id, "", allocation, owner, name,
                    ("__value__",) if scalar else tuple(layout), storage_kind="scalar" if scalar else "reference" if kind == "heap" else "value",
                    embedded_fields=embedded)
                instances[function.method_id].append(instance)
                if not scalar:
                    # 字面量成员与值复制均有逐字段事实，不能再把一个成员的值涂到整个结构体。
                    transfers = [transfer.model_copy(update={"input_variables": []}) if transfer.output_variable == name else transfer
                                 for transfer in transfers]
                    for field in layout:
                        output = name + "." + field
                        definitions.add(output)
                        if not any(transfer.output_variable == output for transfer in transfers):
                            transfers.append(ProgramValueTransfer(output_variable=output, transfer_kind="assignment", provenance="inferred"))
                    pending.extend((name + "." + field, layout[field], "value", depth + 1) for field in embedded)
            if definitions != set(node.definitions) or transfers != node.value_transfers:
                nodes[node.node_id] = node.model_copy(update={"definitions": sorted(definitions), "value_transfers": transfers})
        if nodes != function.nodes:
            updated[function.method_id] = function.model_copy(update={"nodes": nodes})
    return graph.model_copy(update={"functions": updated}), instances


def mark_unknown_storage_effects(function: FunctionProgramGraph, references: ObjectReferenceResolver,
                                   allowed_arguments: frozenset[tuple[str, str]], allowed_calls: frozenset[str],
                                   diagnostics: FlowDiagnostics | None = None) -> FunctionProgramGraph:
    """未知调用后失效引用；保留调用前身份防止退回按文本同名串流，外部调用不擅自视为只读。"""
    nodes = dict(function.nodes)
    for node in function.nodes.values():
        rejected: set[str] = set()
        for call in node.calls:
            addressed = {call.positional_references[position] for position in call.addressed_argument_positions
                         if position < len(call.positional_references)}
            roots = {name for values in (*call.positional_arguments, *call.keyword_arguments.values()) for name in values
                     if (call.callsite_id, name) not in allowed_arguments}
            output = function.nodes.get(CallOutputEffects.node_id(function.method_id, call.callsite_id))
            if output is not None:
                roots.difference_update(output.definitions)
            if call.receiver and call.callsite_id not in allowed_calls:
                roots.add(call.receiver)
            for root in roots:
                for instance in references.possible(node, root, address=root in addressed):
                    rejected.add(instance.allocation_id)
                    if diagnostics is not None:
                        diagnostics.record("storage_unknown_effect", symbol=function.symbol_id, callsite_id=call.callsite_id,
                                           receiver=root, reason_detail="unmodelled_call_may_modify_storage")
        if rejected:
            nodes[node.node_id] = node.model_copy(update={"storage_invalidations": sorted(rejected)})
    return function.model_copy(update={"nodes": nodes})
