"""finally 前冻结返回表达式，完成后只读取快照；不重算表达式或复制整段 finally。"""

from dataclasses import replace
import hashlib

from backend.app.services.value_binding import BindingPattern, BindingValue
from backend.app.services.value_binding.returns import ReturnBinding
from ..conditions import ConditionExpression
from ..contracts import ProgramGraphNode, ProgramValueTransfer


def capture_return(node: ProgramGraphNode, suffix: str) -> tuple[ProgramGraphNode, ProgramGraphNode]:
    """输入尚未完成的 return，输出求值节点和持有原返回形状的延后完成节点。"""
    transfers = []
    expressions = {}
    call_targets = {}

    def freeze(value: BindingValue) -> BindingValue:
        """递归冻结已知聚合成员；调用结果保留调用身份，不能用实参代替返回值。"""
        if value.members:
            members = tuple(replace(member, value=freeze(member.value)) for member in value.members)
            return replace(value, members=members, variables=tuple(dict.fromkeys(name for member in members for name in member.value.variables)))
        if not value.variables and not value.origins and value.kind == "literal":
            return value
        name = "return_snapshot_" + hashlib.sha256((node.node_id + suffix + str(len(transfers))).encode()).hexdigest()[:20]
        expressions[name] = value.expression or ConditionExpression(reason="return_capture_expression_missing")
        transfers.append(ProgramValueTransfer(output_variable=name, input_variables=list(value.variables),
            transfer_kind="call_result" if value.origins else "expression", callsite_ids=list(value.origins), provenance="inferred",
            reference_source=value.expression.name if value.expression is not None and value.expression.kind == "name" else ""))
        for origin in value.origins:
            call_targets[origin] = name
        return replace(value, variables=(name,), origins=(), expression=ConditionExpression(kind="name", name=name, reason=""))

    original = node.return_binding or ReturnBinding(mode="multiple" if len(node.return_values) > 1 else "single",
        values=tuple(BindingValue(variables=tuple(names)) for names in node.return_values))
    binding = replace(original, values=tuple(freeze(value) for value in original.values))
    uses = list(dict.fromkeys(name for value in binding.values for name in value.variables))
    capture = node.model_copy(update={"kind": "assignment", "definitions": [*node.definitions, *expressions],
        "scalar_values": {**node.scalar_values, **expressions}, "value_transfers": [*node.value_transfers, *transfers],
        "return_binding": None, "return_values": [], "return_value_forms": [],
        "calls": [call.model_copy(update={"result_targets": [call_targets[call.callsite_id]],
            "result_pattern": BindingPattern(kind="name", name=call_targets[call.callsite_id])})
            if call.callsite_id in call_targets else call for call in node.calls]})
    resume = node.model_copy(update={"node_id": node.node_id + ":resume:" + suffix, "calls": [], "definitions": [],
        "uses": uses, "value_transfers": [], "scalar_values": {}, "return_binding": binding,
        "return_values": [list(value.variables) for value in binding.values], "provenance": "inferred"})
    return capture, resume
