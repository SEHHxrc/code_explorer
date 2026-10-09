"""已解析调用的参数元素入口 Overlay；不拥有调用目标解析或新的传播算法。"""

from collections import defaultdict

from backend.app.services.program_graph import ProgramGraphArtifact
from backend.app.services.program_graph.passes import (
    ReachingDefinitionsPass,
    ValueFlowPass,
)

from .call_graph import CallTransitionIndex, ProgramArgumentBinder


def prepare_argument_slots(
    graph: ProgramGraphArtifact,
    transitions: CallTransitionIndex,
    binder: ProgramArgumentBinder,
) -> ProgramGraphArtifact:
    """输入原图、已有调用边和参数语义；返回添加静态元素定义的私有图。

    常量实参同样有定义，但没有污点种子；args[0]/args[1] 不互相污染。
    仅为真实目标声明的词法参数派生槽位，原图和形参列表保持不变。
    """
    functions = defaultdict(list)
    for function in graph.functions.values():
        functions[function.symbol_id].append(function)
    additions: dict[str, set[str]] = defaultdict(set)
    precise_returns: dict[str, set[str]] = defaultdict(set)
    for caller in graph.functions.values():
        caller_transitions = transitions.for_source(caller.symbol_id)
        if not caller_transitions:
            continue
        calls = {
            call.callsite_id: call
            for node in caller.nodes.values()
            for call in node.calls
        }
        for transition in caller_transitions:
            call = calls.get(transition.callsite_id)
            if call is None:
                continue
            for target in functions.get(transition.target, ()):
                additions[target.method_id].update(
                    name
                    for name in binder.slot_names(call, target)
                    if any(
                        name.startswith(parameter + "[")
                        for parameter in target.parameters
                    )
                )
                if caller.language == "go" and len(call.result_targets) > 1:
                    precise_returns[caller.method_id].add(call.callsite_id)
    if not any(additions.values()) and not precise_returns:
        return graph
    updated = dict(graph.functions)
    for method_id in additions.keys() | precise_returns.keys():
        slots = additions[method_id]
        function = updated[method_id]
        entry = function.nodes[function.entry_node_id]
        nodes = dict(function.nodes)
        if slots:
            nodes[entry.node_id] = entry.model_copy(
                update={"definitions": sorted(set(entry.definitions) | slots)}
            )
        for node in function.nodes.values():
            if any(
                transfer.transfer_kind == "call_result"
                and set(transfer.callsite_ids).intersection(precise_returns[method_id])
                for transfer in node.value_transfers
            ):
                nodes[node.node_id] = node.model_copy(
                    update={
                        "value_transfers": [
                            transfer.model_copy(update={"input_variables": []})
                            if transfer.transfer_kind == "call_result"
                            and set(transfer.callsite_ids).intersection(
                                precise_returns[method_id]
                            )
                            else transfer
                            for transfer in node.value_transfers
                        ]
                    }
                )
        view = function.model_copy(update={"nodes": nodes})
        updated[method_id] = ValueFlowPass().apply(
            ReachingDefinitionsPass().apply(view)
        )
    return graph.model_copy(update={"functions": updated})
