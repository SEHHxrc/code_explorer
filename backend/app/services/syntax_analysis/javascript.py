"""JS/TS 共享的函数语法和解析器选择；不包含安全规则或图模型。"""

from __future__ import annotations

import json
from typing import Any

from .tree_parser import field, text
from .literals import parse_static_index_literal

JAVASCRIPT_FUNCTION_NODES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "variable_declarator",
        "arrow_function",
        "function_expression",
    }
)


def javascript_function(node: Any, source: bytes) -> tuple[str, Any] | None:
    """返回函数名称和实际函数节点；匿名回调使用源码位置身份，不猜测调用目标。"""
    target = node
    if node.type == "variable_declarator":
        target = field(node, "value")
        if target is None or target.type not in {
            "arrow_function",
            "function_expression",
        }:
            return None
    elif node.type in {"arrow_function", "function_expression"}:
        parent = node.parent
        if parent is not None and parent.type == "variable_declarator":
            return None  # 变量绑定由 declarator 收集一次。
    name = text(field(node, "name"), source)
    if not name:
        name = f"anonymous@{node.start_point[0] + 1}:{node.start_point[1] + 1}"
    return name, target


def javascript_parameters(target: Any, source: bytes) -> list[tuple[str, str, Any]]:
    """返回参数名称、类型和语法节点；保留复杂 pattern 供调用方报告不确定性。"""
    container = field(target, "parameters") or field(target, "parameter")
    if container is None:
        return []
    items = (
        container.named_children
        if container.type == "formal_parameters"
        else [container]
    )
    result = []
    for item in items:
        pattern = (
            field(item, "pattern") or field(item, "name") or field(item, "left") or item
        )
        result.append((text(pattern, source), text(field(item, "type"), source), item))
    return result


def parser_for_file(language: str, path: str) -> str:
    """TSX 使用 TSX grammar，其他文件保持语言本身的 parser。"""
    return (
        "tsx"
        if language == "typescript" and path.lower().endswith(".tsx")
        else language
    )


def javascript_access_name(node: Any, source: bytes) -> str:
    """规范化词法字段/常量下标，与公共 AccessPath 序列化一致；动态下标返回空串。"""
    if node is None:
        return ""
    if node.type in {"identifier", "this"}:
        return text(node, source)
    if node.type not in {"member_expression", "subscript_expression"}:
        return ""
    owner = javascript_access_name(field(node, "object"), source)
    if not owner:
        return ""
    if node.type == "member_expression":
        name = text(field(node, "property"), source)
        return f"{owner}.{name}" if name else ""
    index = parse_static_index_literal(text(field(node, "index"), source))
    if index is None:
        return ""
    if isinstance(index, str) and index.isidentifier():
        return f"{owner}.{index}"
    encoded = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    return f"{owner}[{encoded}]"
