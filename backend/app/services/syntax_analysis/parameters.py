"""纯语法参数适配；不解析调用目标或执行参数值。"""

from typing import Any

from backend.app.services.value_binding.parameters import ParameterKind

from . import field, text


def python_parameter_kinds(node: Any, source: bytes) -> dict[str, ParameterKind]:
    """输入 Tree-sitter parameters，返回位置限定、仅关键字和可变参数类别。"""
    result: dict[str, ParameterKind] = {}
    keyword_only = False
    for item in node.named_children if node is not None else ():
        if item.type == "positional_separator":
            result.update({name: "positional_only" for name in result})
            continue
        if item.type == "keyword_separator":
            keyword_only = True
            continue
        target = field(item, "name") or field(item, "pattern") or item
        pending = [target]
        while pending:
            current = pending.pop()
            if current.type == "identifier":
                name = text(current, source)
                raw = text(item, source).lstrip()
                if raw.startswith("**"):
                    result[name] = "variadic_keyword"
                elif raw.startswith("*"):
                    result[name] = "variadic_positional"
                    keyword_only = True
                else:
                    result[name] = (
                        "keyword_only" if keyword_only else "positional_or_keyword"
                    )
                break
            pending.extend(reversed(current.named_children))
    return result


def java_parameter_parts(node: Any, source: bytes) -> tuple[str, str]:
    """返回 Java 普通/可变参数的名字、类型；spread_parameter 的字段结构不同。"""
    name = field(node, "name")
    type_node = field(node, "type")
    if node.type == "spread_parameter":
        declaration = next(
            (
                item
                for item in node.named_children
                if item.type == "variable_declarator"
            ),
            None,
        )
        name = field(declaration, "name")
        type_node = next(
            (
                item
                for item in node.named_children
                if item.type != "variable_declarator" and item.type != "modifiers"
            ),
            None,
        )
    return text(name, source), text(type_node, source)
