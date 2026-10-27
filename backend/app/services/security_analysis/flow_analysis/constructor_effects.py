"""已解析构造委派的字段投影；复用正常出口摘要，不重新解析语言或生成结构调用边。"""

import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphNode, ProgramValueTransfer
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.semantic_index import SemanticIndex

from .call_graph import ProgramArgumentBinder
from .instance_common import rebuild
from .object_types import ObjectTypes
from .post_calls import append_post_operations


def delegate_fields(function: FunctionProgramGraph, index: SemanticIndex, binder: ProgramArgumentBinder, types: ObjectTypes,
                    summaries: dict[str, list[tuple[str, str, list[FieldValueOrigin]]]],
                    must_writes: dict[str, set[str]], fields: dict[str, set[str]]) -> FunctionProgramGraph:
    """将已有父构造器参数→字段摘要编译到真实委派调用之后；后续覆盖与异常使用原 CFG 规则。

    只消费唯一非截断项目目标和已完成的父摘要；构造循环、重载歧义或未完成摘要不会猜测。
    """
    context = function.instance_context
    if context is None:
        return function
    facts = {fact.callsite_id: fact for fact in index.calls_from(function.symbol_id)}
    operations: dict[str, list[ProgramGraphNode]] = {}
    for node in function.nodes.values():
        for call in node.calls:
            fact = facts.get(call.callsite_id)
            if not call.initializer_kind or fact is None or fact.truncated or len(fact.targets) != 1 or fact.targets[0].truncated:
                continue
            targets = [target for owner in types.constructors.values() for target in owner
                       if target.symbol_id == fact.targets[0].target_symbol and target.method_id in summaries]
            if len(targets) != 1:
                continue
            target = targets[0]
            parent = target.instance_context
            if parent is None or parent.owner_symbol not in types.hierarchy.ancestors(context.owner_symbol):
                continue
            actuals = dict(binder.bind(call, target))
            inputs: dict[str, set[str]] = {}
            origins: dict[str, list[FieldValueOrigin]] = {}
            for parameter, name, details in summaries[target.method_id]:
                output = context.receiver + name[len(parent.receiver):]
                inputs.setdefault(output, set()).update(actuals.get(parameter, ()))
                origins.setdefault(output, []).extend(details)
            for name in fields.get(target.method_id, ()):
                output = context.receiver + name[len(parent.receiver):]
                identity = "initializer-field:" + hashlib.sha256((call.callsite_id + "\x1f" + name).encode()).hexdigest()[:24]
                operations.setdefault(node.node_id, []).append(ProgramGraphNode(node_id=identity, method_id=function.method_id,
                    language=function.language, kind="assignment", location=call.location, code=node.code,
                    definitions=[output], uses=sorted(inputs.get(output, ())),
                    value_transfers=[ProgramValueTransfer(output_variable=output, input_variables=sorted(inputs.get(output, ())),
                                                         transfer_kind="assignment", provenance="inferred")],
                    object_alias_origins={output: origins.get(output, [])}, provenance="inferred",
                    certainty="must" if name in must_writes.get(target.method_id, ()) else "may"))
    if not operations:
        return function
    view = append_post_operations(function, operations)
    return rebuild(view, view.nodes)
