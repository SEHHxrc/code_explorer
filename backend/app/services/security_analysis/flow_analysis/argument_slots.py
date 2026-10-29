"""已解析调用的参数元素入口 Overlay；不拥有调用目标解析或新的传播算法。"""

from collections import defaultdict

from backend.app.services.program_graph import ProgramGraphArtifact
from backend.app.services.program_graph.passes import (
    ReachingDefinitionsPass,
    ValueFlowPass,
)

from backend.app.services.semantic_index import CallTransitionIndex
from .call_graph import ProgramArgumentBinder


def prepare_argument_slots(
    graph: ProgramGraphArtifact, transitions: CallTransitionIndex, binder: ProgramArgumentBinder,
    *, max_rounds: int = 8,
) -> ProgramGraphArtifact:
    """有界传播被调用函数所需的映射成员；复用同一绑定器，固定点后立即停止。"""
    for _ in range(max_rounds):
        updated = _prepare_argument_slots_once(graph, transitions, binder)
        if updated is graph:
            return graph
        graph = updated
    return graph.model_copy(update={"limitations": [*graph.limitations, "字典参数成员需求达到有界传播轮次，较深转发保持未知。"]})


def _prepare_argument_slots_once(
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
    reads: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for caller in graph.functions.values():
        caller_transitions = transitions.for_source(caller.symbol_id)
        if not caller_transitions:
            continue
        calls = {
            call.callsite_id: (node, call)
            for node in caller.nodes.values()
            for call in node.calls
        }
        for transition in caller_transitions:
            hit = calls.get(transition.callsite_id)
            if hit is None:
                continue
            node, call = hit
            for target in functions.get(transition.target, ()):
                missing = ({name for _, names in binder.bind(call, target) for name in names} - set(node.uses)
                           if any(key.startswith("**") for key in call.keyword_arguments) else set())
                if missing:
                    reads[caller.method_id][node.node_id].update(missing)
                additions[target.method_id].update(
                    name
                    for name in binder.slot_names(call, target)
                    if any(
                        name.startswith(parameter + "[")
                        for parameter in target.parameters
                    )
                )
    additions = {key: missing for key, slots in additions.items()
                 if (missing := slots - set(graph.functions[key].nodes[graph.functions[key].entry_node_id].definitions))}
    if not any(additions.values()) and not reads:
        return graph
    updated = dict(graph.functions)
    for method_id in additions.keys() | reads.keys():
        slots = additions.get(method_id, set())
        function = updated[method_id]
        entry = function.nodes[function.entry_node_id]
        nodes = dict(function.nodes)
        for node_id, names in reads[method_id].items():
            node = nodes[node_id]
            nodes[node_id] = node.model_copy(update={"uses": sorted(set(node.uses) | names)})
        if slots:
            nodes[entry.node_id] = entry.model_copy(
                update={"definitions": sorted(set(entry.definitions) | slots)}
            )
        view = function.model_copy(update={"nodes": nodes})
        updated[method_id] = ValueFlowPass().apply(
            ReachingDefinitionsPass().apply(view)
        )
    return graph.model_copy(update={"functions": updated})
