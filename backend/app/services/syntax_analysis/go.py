"""Go 声明的纯语法身份；依赖图、公共程序图和安全前端共用，不含安全规则。"""

from __future__ import annotations

from typing import Any

from .tree_parser import field, normalize_type, text

GO_FUNCTION_NODES = frozenset({"function_declaration", "method_declaration", "func_literal"})


def go_function_name(node: Any, source: bytes) -> str:
    """返回声明名称；匿名函数使用精确位置身份，不借用外层函数或变量名。"""
    return text(field(node, "name"), source) or f"anonymous@{node.start_point[0] + 1}:{node.start_point[1] + 1}"


def go_callable_parts(node: Any, source: bytes) -> tuple[str, ...]:
    """返回词法嵌套的函数身份分段；接收者方法包含声明类型，未解析运行时目标。"""
    ancestors = []
    current = node
    while current is not None:
        if current.type in GO_FUNCTION_NODES:
            ancestors.append(current)
        current = current.parent
    parts: list[str] = []
    for function in reversed(ancestors):
        if function.type == "method_declaration":
            receiver = field(function, "receiver")
            parameter = next((child for child in receiver.named_children if child.type == "parameter_declaration"), None) if receiver is not None else None
            owner = normalize_type(text(field(parameter, "type"), source))
            if owner:
                parts.append(owner)
        parts.append(go_function_name(function, source))
    return tuple(parts)
