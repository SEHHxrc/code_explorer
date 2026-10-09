"""直接嵌套 Source 实参的值槽位；由精确 AST 位置绑定，不猜测包装函数返回值。"""

from backend.app.services.program_graph import ProgramGraphArtifact
from backend.app.services.value_binding import BindingValue

from ..contracts import StaticSecurityFact


def prepare_call_sources(
    graph: ProgramGraphArtifact, sources: list[StaticSecurityFact]
) -> ProgramGraphArtifact:
    """返回私有调用值视图，让 f(source()) 不依赖临时赋值语句。

    仅单返回值 Source、且源调用范围恰好等于一个实参范围时绑定。clean(source()) 的外层
    结果仍需真正的调用/返回传播，不能把 Source 直接送给 clean 外侧的 Sink。
    不创建调用边、不改变持久化图；多返回转发、表达式求值顺序和通用嵌套结果仍未建模。
    """
    source_ids = {
        str(fact.metadata.get("callsite_id"))
        for fact in sources
        if (fact.metadata.get("value_flow") or {}).get("result_role") == "source"
        and not (fact.metadata.get("value_flow") or {}).get("output_arguments")
        and (fact.metadata.get("value_flow") or {}).get("result_count", 1) == 1
    }
    if not source_ids:
        return graph
    updated = dict(graph.functions)
    changed = False
    for function in graph.functions.values():
        nodes = dict(function.nodes)
        for node in function.nodes.values():
            inline = {
                call.location.location_id: call
                for call in node.calls
                if call.callsite_id in source_ids
            }
            if not inline:
                continue
            replacements = {}
            slots = set()
            for call in node.calls:
                positional = list(call.positional_arguments)
                values = list(call.argument_values)
                keywords = dict(call.keyword_arguments)
                for index, location in enumerate(call.positional_argument_locations):
                    source = inline.get(location.location_id)
                    if (
                        source is not None
                        and source.callsite_id != call.callsite_id
                        and index < len(positional)
                    ):
                        name = "call-result:" + source.callsite_id
                        positional[index] = [name]
                        if index < len(values):
                            values[index] = BindingValue(
                                variables=(name,), origins=(source.callsite_id,)
                            )
                        slots.add(name)
                        replacements[source.callsite_id] = replacements.get(
                            source.callsite_id, source
                        ).model_copy(update={"result_variable": name})
                for keyword, location in call.keyword_argument_locations.items():
                    source = inline.get(location.location_id)
                    if source is not None and source.callsite_id != call.callsite_id:
                        name = "call-result:" + source.callsite_id
                        keywords[keyword] = [name]
                        slots.add(name)
                        replacements[source.callsite_id] = replacements.get(
                            source.callsite_id, source
                        ).model_copy(update={"result_variable": name})
                if (
                    positional != call.positional_arguments
                    or keywords != call.keyword_arguments
                ):
                    replacements[call.callsite_id] = replacements.get(
                        call.callsite_id, call
                    ).model_copy(
                        update={
                            "positional_arguments": positional,
                            "argument_values": values,
                            "keyword_arguments": keywords,
                        }
                    )
            if slots:
                nodes[node.node_id] = node.model_copy(
                    update={
                        "definitions": sorted(set(node.definitions) | slots),
                        "calls": [
                            replacements.get(call.callsite_id, call)
                            for call in node.calls
                        ],
                    }
                )
                changed = True
        if nodes != function.nodes:
            updated[function.method_id] = function.model_copy(
                update={
                    "nodes": nodes,
                    "limitations": list(
                        dict.fromkeys(
                            (
                                *function.limitations,
                                "直接嵌套 Source 仅绑定精确实参槽位；通用嵌套返回、复杂表达式和多返回转发未建模。",
                            )
                        )
                    ),
                }
            )
    return graph.model_copy(update={"functions": updated}) if changed else graph
