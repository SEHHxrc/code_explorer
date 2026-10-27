"""集合效果最小接入：原生列表单次追加复用引用环境与调用后置操作，不实现全量集合库。"""

from collections import Counter
from dataclasses import replace
import hashlib

from backend.app.services.program_graph import ProgramGraphArtifact, ProgramGraphNode, ProgramValueTransfer
from backend.app.services.value_binding.collections import CollectionAllocation, CollectionCallView, CollectionEffect, CollectionEffectProvider
from backend.app.services.value_binding.objects import storage_slot
from .diagnostics import FlowDiagnostics
from .instance_common import rebuild
from .object_contracts import LocalInstance
from .object_references import ObjectReferenceIndex
from .object_storage import mark_unknown_storage_effects
from .post_calls import append_post_operations


class BuiltinCollectionEffects:
    """只打样 Python 字面量原生 list.append；并非任意 append 方法，也不展开标准库源码。"""

    def effect(self, language: str, call: CollectionCallView, allocation: CollectionAllocation) -> CollectionEffect | None:
        """已确认可变序列且恰好一个位置实参时返回追加效果；其他操作保持未建模。"""
        if language == "python" and allocation.kind == "sequence" and allocation.mutable and call.name == "append" \
                and len(call.positional_arguments) == 1 and not call.keyword_arguments:
            return CollectionEffect("append", call.receiver, tuple(call.positional_arguments[0]), "python.literal-list.append.v1")
        return None


def prepare_collection_effects(graph: ProgramGraphArtifact, instances: dict[str, list[LocalInstance]],
                               diagnostics: FlowDiagnostics | None = None, *,
                               models: CollectionEffectProvider | None = None) -> ProgramGraphArtifact:
    """编译唯一已知分配的单次正常追加；循环、多次追加及未知布局明确拒绝，不猜测长度。

    这是公共效果接口的闭环样例；原引用、未知副作用检测、CFG 后置时序及 RD/DFG 均复用。
    输入图不修改，instances 是本次私有布局表；未支持的调用仍由原未知副作用机制处理。
    """
    provider = models or BuiltinCollectionEffects()
    functions = dict(graph.functions)
    for function in graph.functions.values():
        local = instances.get(function.method_id, [])
        if not local or not any(item.collection is not None for item in local):
            continue
        if models is None and (function.language != "python" or not any(
                call.name == "append" for node in function.nodes.values() for call in node.calls)):
            continue
        references = ObjectReferenceIndex(function, local)
        candidates = []
        for node in function.nodes.values():
            for call in node.calls:
                possible = references.possible(node, call.receiver)
                if len(possible) == 1 and possible[0].collection is not None:
                    effect = provider.effect(function.language, call, possible[0].collection)
                    if effect is not None:
                        candidates.append((node, call, possible[0], effect))
        if not candidates:
            continue
        counts = Counter(item.allocation_id for _, _, item, _ in candidates)
        allowed = frozenset(call.callsite_id for _, call, _, _ in candidates)
        arguments = frozenset((call.callsite_id, name) for _, call, _, _ in candidates
                              for values in call.positional_arguments for name in values)
        # 先检查调用前是否已经逃逸；不因列表最初来自字面量就忽略未知函数对它的修改。
        checked = mark_unknown_storage_effects(function, references, arguments, allowed)
        references = ObjectReferenceIndex(checked, local)
        additions, nodes = {}, dict(function.nodes)
        for node, call, original, effect in candidates:
            possible = references.possible(node, call.receiver)
            instance = possible[0] if len(possible) == 1 else None
            allocation = instance.collection if instance is not None else None
            if (counts[original.allocation_id] != 1 or instance is None or allocation is None or not allocation.complete
                    or node.node_id in references.cyclic_nodes or len(node.calls) != 1 or node.kind == "return"
                    or set(effect.value_names) & set(node.definitions)
                    or effect.operation != "append" or len(allocation.members) >= 256
                    or set(allocation.members) != {"[" + str(index) + "]" for index in range(len(allocation.members))}):
                if diagnostics is not None:
                    diagnostics.record("collection_effect_not_modelled", symbol=function.symbol_id,
                        callsite_id=call.callsite_id, reason_detail="cycle_multiple_append_escape_or_unknown_layout")
                continue
            member = "[" + str(len(allocation.members)) + "]"
            output = call.receiver + member
            key = hashlib.sha256(call.callsite_id.encode()).hexdigest()[:24]
            post = ProgramGraphNode(node_id="collection-append:" + key, kind="operation", language=function.language,
                method_id=function.method_id, location=call.location, code=node.code, provenance="inferred",
                definitions=[output], uses=list(effect.value_names), definition_object_slots={output: storage_slot(instance.allocation_id, member)},
                value_transfers=[ProgramValueTransfer(output_variable=output, input_variables=list(effect.value_names), transfer_kind="assignment",
                    reference_source=call.positional_references[0] if call.positional_references else "", provenance="inferred")])
            additions.setdefault(node.node_id, []).append(post)
            nodes[node.node_id] = node.model_copy(update={"calls": [item.model_copy(update={"collection_effect_model": effect.model_id})
                if item.callsite_id == call.callsite_id else item for item in node.calls],
                "value_transfers": [transfer.model_copy(update={"input_variables": []})
                    if call.callsite_id in transfer.callsite_ids else transfer for transfer in node.value_transfers]})
            # 长度变化后不沿用全函数固定长度做负下标证明；固定位置仍精确，负位置退为 may 读取。
            instances[function.method_id] = [replace(item, fields=(*item.fields, member),
                collection=item.collection.model_copy(update={"complete": False})) if item.allocation_id == instance.allocation_id else item
                for item in instances[function.method_id]]
        if additions:
            view = append_post_operations(function.model_copy(update={"nodes": nodes}), additions)
            functions[function.method_id] = rebuild(view, view.nodes)
    return graph.model_copy(update={"functions": functions})
