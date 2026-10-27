"""精确调用结果槽位；替代 Source 专用逻辑，不重建调用目标或持久化图。"""

from collections import defaultdict
from dataclasses import replace

from backend.app.services.program_graph import ProgramGraphArtifact, ProgramValueTransfer
from backend.app.services.program_graph.passes import ReachingDefinitionsPass, ValueFlowPass
from backend.app.services.value_binding import BindingValue
from backend.app.services.value_binding.returns import resolve_return
from backend.app.services.value_binding.expressions import ConditionExpression

from ..contracts import StaticSecurityFact
from backend.app.services.semantic_index import CallTransitionIndex
from .return_bindings import bind_return_calls, result_patterns, return_binding
from .diagnostics import FlowDiagnostics


def prepare_call_results(
    graph: ProgramGraphArtifact,
    sources: list[StaticSecurityFact],
    transitions: CallTransitionIndex,
    *, object_calls: set[str] | None = None,
    diagnostics: FlowDiagnostics | None = None,
) -> ProgramGraphArtifact:
    """为精确嵌套实参隔离返回值，并禁止已解析项目调用的输入直接冒充返回。

    已知单返回 Source 可直接产生结果；项目调用必须经真实 return 恢复结果。
    未解析的第三方调用仍保守传播；单值聚合与多返回统一走返回边界契约。
    本 Overlay 使用公共调用位置和值传递协议，各语言共用，不修改输入图。
    """
    source_ids = {
        str(fact.metadata.get("callsite_id"))
        for fact in sources
        if (fact.metadata.get("value_flow") or {}).get("result_role") == "source"
        and not (fact.metadata.get("value_flow") or {}).get("output_arguments")
        and (fact.metadata.get("value_flow") or {}).get("result_count", 1) == 1
    }
    symbols = {function.symbol_id for function in graph.functions.values()}
    returns_by_symbol = defaultdict(list)
    for function in graph.functions.values():
        returns_by_symbol[function.symbol_id].extend(
            node for node in function.nodes.values() if node.kind == "return"
        )
    updated = dict(graph.functions)
    for function in graph.functions.values():
        project_ids = {
            transition.callsite_id for transition in transitions.for_source(function.symbol_id)
            if transition.target in symbols
        }
        targets = defaultdict(set)
        for transition in transitions.for_source(function.symbol_id):
            targets[transition.callsite_id].add(transition.target)
        # 创建对象不是将初始化实参原样返回；字段模型未覆盖时也不能退回整对象污染。
        project_ids.update(call.callsite_id for node in function.nodes.values() for call in node.calls
                           if call.callsite_id in (object_calls or ()))
        eligible = source_ids | project_ids
        nodes = dict(function.nodes)
        for node in function.nodes.values():
            inline = {
                call.location.location_id: call
                for call in node.calls if call.callsite_id in eligible
            }
            if not inline:
                continue
            replacements = {}
            slots: set[str] = set()
            returns = list(node.return_values)
            binding = return_binding(node) if node.kind == "return" else None
            for index, location in enumerate(node.return_value_locations):
                child = inline.get(location.location_id)
                if child is not None and index < len(returns):
                    name = "call-result:" + child.callsite_id
                    returns[index] = [name]
                    if binding is not None and index < len(binding.values):
                        parts = list(binding.values)
                        parts[index] = BindingValue(variables=(name,), expression=ConditionExpression(kind="name", name=name, reason=""))
                        binding = replace(binding, values=tuple(parts))
                    slots.add(name)
                    replacements[child.callsite_id] = child.model_copy(update={"result_variable": name})
            if binding is not None:
                binding, used_calls = bind_return_calls(binding, eligible)
                if used_calls:
                    returns = [list(value.variables) for value in binding.values]
                    for child in node.calls:
                        if child.callsite_id in used_calls:
                            name = "call-result:" + child.callsite_id
                            slots.add(name)
                            replacements[child.callsite_id] = child.model_copy(update={"result_variable": name})
            for call in node.calls:
                positional = list(call.positional_arguments)
                values = list(call.argument_values)
                keywords = dict(call.keyword_arguments)
                for index, location in enumerate(call.positional_argument_locations):
                    child = inline.get(location.location_id)
                    if child is None or child.callsite_id == call.callsite_id or index >= len(positional):
                        continue
                    name = "call-result:" + child.callsite_id
                    positional[index] = [name]
                    if index < len(values):
                        values[index] = BindingValue(variables=(name,), origins=(child.callsite_id,))
                    slots.add(name)
                    replacements[child.callsite_id] = replacements.get(child.callsite_id, child).model_copy(
                        update={"result_variable": name}
                    )
                for keyword, location in call.keyword_argument_locations.items():
                    child = inline.get(location.location_id)
                    if child is None or child.callsite_id == call.callsite_id:
                        continue
                    name = "call-result:" + child.callsite_id
                    keywords[keyword] = [name]
                    slots.add(name)
                    replacements[child.callsite_id] = replacements.get(child.callsite_id, child).model_copy(
                        update={"result_variable": name}
                    )
                if positional != call.positional_arguments or keywords != call.keyword_arguments:
                    replacements[call.callsite_id] = replacements.get(call.callsite_id, call).model_copy(update={
                        "positional_arguments": positional, "argument_values": values,
                        "keyword_arguments": keywords,
                    })
            # 直接赋值的已解析调用也必须等待 return；不能使用保守的输入→结果捷径。
            transfers = [
                transfer.model_copy(update={"input_variables": []})
                if transfer.transfer_kind == "call_result"
                and transfer.callsite_ids
                and transfer.callsite_ids[0] in project_ids
                else transfer
                for transfer in node.value_transfers
            ]
            # 只有所有可能 return 都观察到的接收路径才可建立精确子定义。
            # 否则一个已知分支不能掩盖另一个未知形状分支的保守父值传播。
            for original in node.calls:
                call = replacements.get(original.callsite_id, original)
                if call.callsite_id not in project_ids:
                    continue
                patterns = result_patterns(call, tuple(call.result_targets))
                outputs: set[str] | None = None
                for symbol in targets[call.callsite_id]:
                    return_nodes = returns_by_symbol.get(symbol, ())
                    if not return_nodes:
                        outputs = set()
                    for returned in return_nodes:
                        branch_outputs: set[str] = set()
                        for pattern in patterns:
                            resolution = resolve_return(return_binding(returned), pattern)
                            branch_outputs.update(item.output for item in resolution.projections)
                            if diagnostics is not None:
                                for reason in resolution.diagnostics:
                                    diagnostics.record("return_binding_not_modelled", symbol=function.symbol_id,
                                        callsite_id=call.callsite_id, return_node_id=returned.node_id, reason_detail=reason)
                        outputs = branch_outputs if outputs is None else outputs & branch_outputs
                for name in sorted((outputs or set()) - set(node.definitions) - slots):
                    slots.add(name)
            if not slots and not replacements and transfers == node.value_transfers:
                continue
            transfers.extend(
                ProgramValueTransfer(output_variable=name, transfer_kind="call_result",
                                     input_variables=[], certainty="may", provenance="inferred")
                for name in sorted(slots)
            )
            nodes[node.node_id] = node.model_copy(update={
                "definitions": sorted(set(node.definitions) | slots),
                "calls": [replacements.get(call.callsite_id, call) for call in node.calls],
                "value_transfers": transfers,
                "return_values": returns,
                "return_binding": binding,
            })
        if nodes != function.nodes:
            view = function.model_copy(update={"nodes": nodes})
            updated[function.method_id] = ValueFlowPass().apply(ReachingDefinitionsPass().apply(view))
    return graph.model_copy(update={"functions": updated}) if updated != graph.functions else graph
