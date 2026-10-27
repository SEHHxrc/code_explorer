"""Python 框架回调适配；只产生公共 may 值边界，不建立另一套传播算法。"""

import hashlib
from collections import defaultdict
from dataclasses import replace

from ..ir import IRFunction, IRValueBoundary, SecurityProgramIR
from ..semantics.python import PythonLanguageSemantics

BACKGROUND_TASK_TYPES = frozenset({
    "fastapi.BackgroundTasks", "fastapi.background.BackgroundTasks",
    "starlette.background.BackgroundTasks",
})


def collect_python_value_boundaries(
    program: SecurityProgramIR, rebound_names: dict[str, set[str]],
) -> list[IRValueBoundary]:
    """把已标注 BackgroundTasks 的 add_task 实参映射到同文件顶层函数参数。

    输入已解析 IR 和被赋值名称；仅接受唯一、未遮蔽的静态函数引用及无调用实参。
    普通位置/关键字绑定复用现有语言语义；不猜测导入回调、lambda、对象方法、
    任意调度器、复杂实参返回值或任务是否实际执行。不支持的注册保留定位诊断。
    """
    functions = {item.symbol: item for item in program.functions}
    top_level: dict[tuple[str, str], list[IRFunction]] = defaultdict(list)
    for function in program.functions:
        if function.symbol.count("::") == 1:
            top_level[(function.location.path, function.name)].append(function)
    semantics = PythonLanguageSemantics()
    boundaries: list[IRValueBoundary] = []
    for call in program.calls:
        caller = functions.get(call.symbol)
        if caller is None or not call.qualified_name.endswith(".add_task"):
            continue
        receivers = {item.name for item in caller.parameters if item.annotation in BACKGROUND_TASK_TYPES}
        if call.receiver not in receivers or call.receiver in rebound_names.get(caller.symbol, set()):
            continue
        callback = call.arguments[0] if call.arguments else call.keyword("func")
        targets = top_level.get((call.location.path, callback.text if callback else ""), [])
        shadowed = rebound_names.get(caller.symbol, set()) | {item.name for item in caller.parameters}
        if callback is None or callback.kind != "name" or callback.text in shadowed or len(targets) != 1:
            program.failures.append({"reason": "python_background_callback_not_modelled",
                                     "path": call.location.path, "line": call.location.line,
                                     "callsite_id": call.callsite_id})
            continue
        target = targets[0]
        forwarded = replace(call, arguments=call.arguments[1:] if call.arguments else (),
                            keywords=tuple((key, value) for key, value in call.keywords if call.arguments or key != "func"))
        for binding in semantics.bind_arguments(forwarded, target):
            value = (forwarded.arguments[binding.argument_position] if binding.argument_position is not None
                     else forwarded.keyword(binding.argument_keyword))
            if value is None or value.is_literal:
                continue
            if value.contains_call or value.kind == "starred":
                program.failures.append({"reason": "python_background_argument_not_modelled",
                                         "path": call.location.path, "line": call.location.line,
                                         "callsite_id": call.callsite_id, "parameter": binding.parameter_name})
                continue
            if not value.identifiers:
                continue
            identity = "\x1f".join((call.callsite_id, target.symbol, binding.parameter_name))
            boundaries.append(IRValueBoundary(
                boundary_id="boundary:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                kind="fastapi_background_argument", source_scope=caller.location,
                source_location=call.location, source_names=value.identifiers,
                target_scope=target.location, target_location=target.location,
                target_names=(binding.parameter_name,), capture=True,
                limitations=("BackgroundTasks 仅建立显式参数的 may 调度绑定，不证明任务实际执行、顺序或完整生命周期；回调限同文件唯一顶层函数。",),
            ))
    return boundaries
