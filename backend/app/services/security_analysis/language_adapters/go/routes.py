"""Go 路由注册与真实处理器声明关联；不推断认证要求或运行时可达。"""

from dataclasses import replace
from pathlib import PurePosixPath

from ...ir import SecurityProgramIR
from .api import ROUTERS, ROUTE_METHODS


def resolve_routes(program: SecurityProgramIR) -> None:
    """原位关联唯一定义，歧义登记诊断；同包同名不同接收者不会串联。"""
    functions: dict[tuple[str, str, str], list[str]] = {}
    for function in program.functions:
        key = (str(PurePosixPath(function.location.path).parent),
               function.owner_types[0] if function.owner_types else "", function.name)
        functions.setdefault(key, []).append(function.symbol)
    for index, call in enumerate(program.calls):
        route = (call.receiver_api_type in ROUTERS and call.qualified_name.rsplit(".", 1)[-1] in ROUTE_METHODS
                 or call.qualified_name in {"http.Handle", "http.HandleFunc", "http.ServeMux.Handle", "http.ServeMux.HandleFunc"})
        if not route or not call.arguments:
            continue
        handler = call.arguments[-1]
        handler_name = "ServeHTTP" if handler.api_type and handler.kind not in {"identifier", "selector_expression", "function"} else handler.text.rsplit(".", 1)[-1]
        handler_owner = handler.api_type if handler_name == "ServeHTTP" else handler.callable_owner_type
        names = [handler.callable_symbol] if handler.callable_symbol else functions.get(
            (str(PurePosixPath(call.location.path).parent), handler_owner, handler_name), [])
        if handler.callable_unresolved:
            names = []
        if len(names) == 1:
            program.calls[index] = replace(call, handler_symbols=tuple(names))
        else:
            program.failures.append({"path": call.location.path, "line": call.location.line,
                                     "reason": "go_route_handler_not_uniquely_resolved", "callsite_id": call.callsite_id})
