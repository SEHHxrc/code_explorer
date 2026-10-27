"""通过已有调用目标与参数协议传递 API 上下文；不创建调用边或整对象污点。"""

from collections import defaultdict, deque
from dataclasses import replace
from backend.app.services.semantic_index import CallTransitionView

from .ir import IRCall, IRFunction, SecurityProgramIR
from .semantics.base import LanguageSemantics

def forward_parameter_apis(
    program: SecurityProgramIR, transitions: CallTransitionView,
    semantics: LanguageSemantics, source_names: set[str], *, max_depth: int = 4,
) -> None:
    """把已知参数 API 角色沿明确具名实参转发，用现有规则识别被调用侧字段读取。

    输入语言无关 IR、既有调用解析、绑定语义和已注册 Source API 名称；原地更新 IR
    的访问资格化，不产生新 Source 种类或 CALLS，不把 request 整体设为污点。
    仅支持未重绑定的普通具名参数、直接实参；返回值、别名、解构及运行时类型不猜测。
    所有转发均为 may，保留调用点 ID；没有已解析目标时不按同名函数补边。
    """
    functions: dict[str, list[IRFunction]] = defaultdict(list)
    calls: dict[str, dict[str, IRCall]] = defaultdict(dict)
    for function in program.functions:
        functions[function.symbol].append(function)
    for call in program.calls:
        calls[call.symbol][call.callsite_id] = call
    bindings: dict[tuple[str, str, str], tuple[str, ...]] = {}
    pending = deque()
    for function in program.functions:
        for parameter in function.parameters:
            if parameter.api_type and not parameter.reassigned:
                key = (function.symbol, parameter.name, parameter.api_type)
                bindings[key] = ()
                pending.append(key)
    while pending:
        symbol, parameter, api_type = pending.popleft()
        route = bindings[(symbol, parameter, api_type)]
        if len(route) >= max_depth:
            if any(item.target in functions for item in transitions.for_source(symbol)):
                program.failures.append({"reason": "parameter_api_depth_limit", "symbol": symbol,
                                         "parameter": parameter, "limit": max_depth,
                                         "callsite_ids": list(route)})
            continue
        for transition in transitions.for_source(symbol):
            call = calls[symbol].get(transition.callsite_id)
            if call is None:
                continue
            for target in functions.get(transition.target, []):
                parameters = {item.name: item for item in target.parameters}
                for binding in semantics.bind_arguments(call, target):
                    destination = parameters.get(binding.parameter_name)
                    if destination is None or destination.reassigned:
                        continue
                    value = (call.arguments[binding.argument_position]
                             if binding.argument_position is not None and binding.argument_position < len(call.arguments)
                             else call.keyword(binding.argument_keyword) if binding.argument_keyword else None)
                    if value is None or value.kind not in {"identifier", "name"} or value.text != parameter:
                        continue
                    key = (target.symbol, destination.name, api_type)
                    if key not in bindings:
                        bindings[key] = (*route, call.callsite_id)
                        pending.append(key)
    by_parameter: dict[tuple[str, str], list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    for (symbol, parameter, api_type), route in bindings.items():
        by_parameter[(symbol, parameter)].append((api_type, route))
    for index, access in enumerate(program.accesses):
        if access.access_kind != "read":
            continue
        root, separator, tail = access.qualified_name.partition(".")
        if not separator:
            continue
        matches = {
            (f"{api_type}.{tail.split('.', 1)[0]}" if f"{api_type}.{tail.split('.', 1)[0]}" in source_names else api_type, route)
            for api_type, route in by_parameter.get((access.symbol, root), [])
            if f"{api_type}.{tail.split('.', 1)[0]}" in source_names or api_type in source_names
        }
        # 不同静态 API 角色有歧义时不猜测；同一角色保留最短真实转发序列。
        names = {name for name, _ in matches}
        if len(names) == 1:
            name, route = min(matches, key=lambda item: (len(item[1]), item))
            program.accesses[index] = replace(access, qualified_name=name, binding_certainty="may",
                                               resolution_method="resolved_parameter_forwarding",
                                               api_binding_callsite_ids=route)
        elif len(names) > 1:
            program.failures.append({"reason": "parameter_api_role_ambiguous", "path": access.location.path,
                                     "line": access.location.line, "api_names": sorted(names)})
