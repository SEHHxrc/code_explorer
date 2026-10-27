"""下标常量语法适配；所有语言共用表达式契约，不在 AST 遍历时猜测运行时变量值。"""

import hashlib
from dataclasses import replace
from typing import Any

from backend.app.services.syntax_analysis import field, semantic_children
from backend.app.services.value_binding.access_paths import AccessPath, AccessPathSegment, parse_static_index_literal
from backend.app.services.value_binding.selectors import SelectorExpression
from ..control_ir import ControlStatement, ControlValueTransfer


def selector_expression(collector: Any, node: Any | None, depth: int = 0) -> SelectorExpression:
    """提取字面量、名称、括号和条件选择；算术、调用及超限不执行而保留未知。"""
    if node is None or depth >= 8:
        return SelectorExpression()
    children = semantic_children(node)
    if node.type in {"parenthesized_expression", "literal_element"} and len(children) == 1:
        return selector_expression(collector, children[0], depth + 1)
    value = parse_static_index_literal(collector._text(node))
    if collector.language == "java" and node.type == "character_literal":
        value = collector._static_array_index(node)
    if value is not None:
        return SelectorExpression(kind="literal", value=value)
    if node.type == "identifier":
        return SelectorExpression(kind="name", name=collector._text(node))
    if node.type in {"conditional_expression", "ternary_expression"} and len(children) == 3:
        left = field(node, "consequence") or field(node, "body") or children[0]
        right = field(node, "alternative") or children[-1]
        return SelectorExpression(kind="choice", choices=(selector_expression(collector, left, depth + 1),
                                                          selector_expression(collector, right, depth + 1)))
    return SelectorExpression()


def indexed_path(collector: Any, owner: AccessPath | None, index: Any | None, node: Any,
                 constant: str | int | None) -> AccessPath | None:
    """已知下标沿用精确路径；简单动态下标携带位置私有选择器，不与真实字符串键混用。"""
    if owner is None or index is None or index.type in {"slice", "slice_expression"}:
        return None
    if constant is not None:
        return owner.index(constant)
    if collector.language not in {"python", "java", "go", "javascript", "typescript", "tsx"}:
        return None
    key = "selector_" + hashlib.sha256((collector.path + ":" + str(node.start_byte)).encode()).hexdigest()[:16]
    if not hasattr(collector, "_access_selectors"):
        collector._access_selectors = {}
    collector._access_selectors[key] = selector_expression(collector, index)
    return AccessPath(owner.root, (*owner.segments, AccessPathSegment("selector", key)))


def decorate_selectors(collector: Any, statement: ControlStatement, bindings: list[tuple[Any, Any]]) -> ControlStatement:
    """将简单标量赋值和本操作使用的选择器保存到公共 IR，复用同一声明/CFG 求值。"""
    definitions = {collector._direct_reference(target): expression for target, value in bindings
                   if collector._direct_reference(target).isidentifier()
                   and (expression := selector_expression(collector, value)).kind != "unknown"}
    names = (*statement.uses, *statement.definitions)
    selectors = {key: value for key, value in getattr(collector, "_access_selectors", {}).items()
                 if any("[?" + key + "]" in name for name in names)}
    transfers = list(statement.value_transfers)
    for target, value in bindings:
        output = collector._direct_reference(target)
        if "[?" not in output or value.type not in collector.call_node_types or any(item.output_variable == output for item in transfers):
            continue
        # 无参 Source 调用也必须保留精确 RHS 调用身份；下标中调用不属于写入元素的来源。
        calls = tuple(call.callsite_id for call in statement.calls if
            (call.location.line, call.location.column, call.location.end_line, call.location.end_column) ==
            (value.start_point[0] + 1, value.start_point[1] + 1, value.end_point[0] + 1, value.end_point[1] + 1))
        if calls:
            transfers.append(ControlValueTransfer(output_variable=output, input_variables=collector._identifiers(value),
                transfer_kind="call_result", callsite_ids=calls))
    return replace(statement, selector_definitions=tuple(definitions.items()), access_selectors=tuple(selectors.items()), value_transfers=tuple(transfers),
        uses=collector._unique((*statement.uses, *(name for value in selectors.values() for name in value.names()))))
