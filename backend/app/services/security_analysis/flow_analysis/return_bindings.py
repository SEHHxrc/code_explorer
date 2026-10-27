"""程序图返回契约的兼容投影；不增加另一套污点求解器。"""

from dataclasses import replace
from backend.app.services.program_graph import ProgramCallSite, ProgramGraphNode
from backend.app.services.value_binding import BindingPattern, BindingValue, PatternMember
from backend.app.services.value_binding.returns import ReturnBinding
from backend.app.services.value_binding.expressions import ConditionExpression


def return_binding(node: ProgramGraphNode) -> ReturnBinding:
    """优先真实形状；旧产物仅保留已有分量，不能补造元组结构。"""
    if node.return_binding is not None:
        return node.return_binding
    parts = node.return_values or [node.uses]
    return ReturnBinding(
        mode="multiple" if len(parts) > 1 else "single",
        values=tuple(BindingValue(variables=tuple(part)) for part in parts),
    )


def result_patterns(call: ProgramCallSite, names: tuple[str, ...]) -> tuple[BindingPattern, ...]:
    """按精确临时槽、显式接收模式、旧有序槽、旧结果名称的顺序选择。"""
    if call.result_variable:
        return (BindingPattern(kind="name", name=call.result_variable),)
    if call.result_pattern is not None:
        return (call.result_pattern,)
    if len(call.result_targets) > 1:
        return (BindingPattern(kind="array", members=tuple(
            PatternMember(index, BindingPattern(kind="name", name=name))
            for index, name in enumerate(call.result_targets)
        )),)
    return tuple(BindingPattern(kind="name", name=name) for name in names if name)


def bind_return_calls(binding: ReturnBinding, eligible: set[str]) -> tuple[ReturnBinding, set[str]]:
    """按前端提供的精确调用身份替换聚合成员，返回新形状与用到的调用 ID。"""
    used: set[str] = set()

    def visit(value: BindingValue) -> BindingValue:
        """只替换直接调用；容器重新汇总子值，不猜测任意表达式内的值变换。"""
        calls = set(value.origins) & eligible
        if len(calls) == 1:
            callsite = next(iter(calls))
            used.add(callsite)
            name = "call-result:" + callsite
            return BindingValue(variables=(name,), origins=value.origins,
                expression=ConditionExpression(kind="name", name=name, reason=""))
        if not value.members:
            return value
        members = tuple(replace(member, value=visit(member.value)) for member in value.members)
        return replace(value, members=members, variables=tuple(dict.fromkeys(
            name for member in members for name in member.value.variables
        ))) if members != value.members else value

    return replace(binding, values=tuple(visit(value) for value in binding.values)), used
