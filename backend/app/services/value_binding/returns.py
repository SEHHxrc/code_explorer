"""返回边界契约：区分一个聚合值与 ABI 多分量，复用公共模式投影。"""

from dataclasses import dataclass
from typing import Literal

from .contracts import BindingPattern, BindingResolution, BindingValue, ValueMember
from .resolver import StructuredBindingResolver


@dataclass(frozen=True)
class ReturnBinding:
    """返回表达式的值形状，不是类型或污点结论。

    single 的唯一分量可为元组/数组等聚合值；multiple 表示原生多返回 ABI。
    unknown 表示前端没有足够事实；不能将其当作已知的空值或安全常量。
    """

    mode: Literal["single", "multiple", "unknown"] = "unknown"
    values: tuple[BindingValue, ...] = ()


def resolve_return(binding: ReturnBinding, receiver: BindingPattern) -> BindingResolution:
    """输入返回形状与接收模式，输出值投影及缺口；不解析调用或运行表达式。"""
    if binding.mode == "unknown" or not binding.values:
        return BindingResolution(diagnostics=("return_shape_unknown",))
    if binding.mode == "single":
        if len(binding.values) != 1:
            return BindingResolution(diagnostics=("invalid_single_return_arity",))
        value = binding.values[0]
    else:
        if receiver.kind != "array":
            return BindingResolution(diagnostics=("multiple_return_requires_component_receivers",))
        value = BindingValue(kind="array", members=tuple(
            ValueMember(index, part) for index, part in enumerate(binding.values)
        ))
    # 固定序列解包不能把越界当作一个安全的 missing 值；rest 交给现有绑定器。
    pending = [(receiver, value)]
    while pending:
        target, current = pending.pop()
        if target.kind != "array" or current.kind != "array" or current.uncertain:
            continue
        has_rest = any(member.pattern.kind == "rest" for member in target.members)
        if not has_rest and tuple(member.selector for member in target.members) != tuple(member.selector for member in current.members):
            return BindingResolution(diagnostics=("return_receiver_arity_mismatch",))
        if has_rest and len(current.members) < len(target.members) - 1:
            return BindingResolution(diagnostics=("return_receiver_arity_mismatch",))
        known = {member.selector: member.value for member in current.members}
        pending.extend((member.pattern, known[member.selector]) for member in target.members
                       if member.pattern.kind != "rest" and member.selector in known)
    return StructuredBindingResolver().resolve(receiver, value)
