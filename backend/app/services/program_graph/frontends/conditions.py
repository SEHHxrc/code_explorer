"""复用各语言 Tree-sitter 树，提取公共条件；不重新解析源码、不执行表达式。"""

import ast
from dataclasses import replace
from typing import Any

from backend.app.services.syntax_analysis import field, semantic_children
from backend.app.services.value_binding.access_paths import canonical_path
from ..conditions import ConditionExpression
from ..control_ir import ControlStatement

_OPERATORS = {"==": "eq", "===": "eq", "!=": "ne", "!==": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_ARITHMETIC = {"+": "add", "-": "sub", "*": "mul"}


def condition_expression(collector: Any, node: Any | None, depth: int = 0) -> ConditionExpression:
    """降低已知标量/访问路径、布尔比较和受支持算术；不执行调用或猜测隐式转换。"""
    if node is None or depth >= 16:
        return ConditionExpression(reason="condition_missing_or_depth_limit")
    children = semantic_children(node)
    text = collector._text(node)
    if node.type in {"parenthesized_expression", "condition_clause", "expression_list", "literal_element"} and len(children) == 1:
        return condition_expression(collector, children[0], depth + 1)
    if node.type in {"true", "false", "boolean_literal"} and text in {"True", "False", "true", "false"}:
        return ConditionExpression(kind="literal", value=text.lower() == "true", reason="")
    if node.type == "identifier":
        return ConditionExpression(kind="name", name=text, reason="")
    reference = collector._direct_reference(node)
    if reference and canonical_path(reference) is not None:
        return ConditionExpression(kind="name", name=reference, reason="")
    if node.type in {"integer", "integer_literal", "int_literal", "number", "number_literal", "decimal_integer_literal", "string", "string_literal", "interpreted_string_literal"}:
        if len(text) > 4096:
            return ConditionExpression(reason="condition_literal_size_limit")
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            value = None
        if type(value) in {str, int}:
            return ConditionExpression(kind="literal", value=value, reason="")
    operator_node = field(node, "operator")
    operator = collector._text(operator_node) if operator_node is not None else ""
    if not operator:
        operator = next((collector._text(item) for item in node.children
                         if not item.is_named and collector._text(item) in {*_OPERATORS, *_ARITHMETIC, "and", "or", "not", "&&", "||", "!"}), "")
    # JS 松散相等存在类型强制转换；Java == 对引用不是字符串值相等。
    if collector.language in {"javascript", "typescript", "tsx"} and operator in {"==", "!="}:
        return ConditionExpression(reason="coercive_equality_not_modelled")
    if operator not in {*_OPERATORS, *_ARITHMETIC, "!", "not", "and", "&&", "or", "||"} or len(children) > 2:
        return ConditionExpression(reason="condition_expression_not_modelled:" + node.type)
    operands = tuple(condition_expression(collector, item, depth + 1) for item in children)
    if collector.language == "java" and operator in {"==", "!="} and any(type(item.value) is str for item in operands):
        return ConditionExpression(reason="java_reference_equality_not_modelled")
    if operator in {"!", "not"} and len(operands) == 1:
        return ConditionExpression(kind="not", operands=operands, reason="")
    if operator in {"and", "&&", "or", "||"} and len(operands) == 2:
        return ConditionExpression(kind="and" if operator in {"and", "&&"} else "or", operands=operands, reason="")
    if operator in _OPERATORS and len(operands) == 2:
        return ConditionExpression(kind="compare", operator=_OPERATORS[operator], operands=operands, reason="")
    if operator in _ARITHMETIC and len(operands) == 2:
        return ConditionExpression(kind="arithmetic", operator=_ARITHMETIC[operator], operands=operands, reason="")
    if operator == "-" and len(operands) == 1:
        return ConditionExpression(kind="arithmetic", operator="neg", operands=operands, reason="")
    return ConditionExpression(reason="condition_expression_not_modelled:" + node.type)


def loop_condition(collector: Any, node: Any, condition: Any | None) -> ConditionExpression | None:
    """只降低真实前置条件；迭代器、for 初始化/更新和后置循环不能冒充普通 while。"""
    if node.type in {"while_statement", "while_expression"}:
        return condition_expression(collector, condition)
    if collector.language == "go" and node.type == "for_statement":
        if condition is None:
            return ConditionExpression(kind="literal", value=True, reason="")
        if condition.type not in {"for_clause", "range_clause"}:
            return condition_expression(collector, condition)
    if collector.language == "rust" and node.type == "loop_expression":
        return ConditionExpression(kind="literal", value=True, reason="")
    return None


def decorate_conditions(collector: Any, statement: ControlStatement, bindings: tuple[tuple[Any, Any, bool], ...]) -> ControlStatement:
    """保存直接标量和规范成员赋值用于版本更新；复合赋值/解构不猜计算结果。"""
    values = {collector._direct_reference(target): condition_expression(collector, value)
              for target, value, compound in bindings if not compound and collector._direct_reference(target)
              and canonical_path(collector._direct_reference(target)) is not None}
    return replace(statement, scalar_values=tuple(values.items()))


def parameter_defaults(collector: Any, node: Any) -> dict[str, ConditionExpression]:
    """仅保存声明中直接字面量默认值；Python 定义时求值与 JS 调用时求值不混猜。"""
    parameters = field(node, "parameters")
    if parameters is None:
        return {}
    result = {}
    for item in parameters.named_children:
        target = field(item, "name") or field(item, "left") or field(item, "pattern")
        value = field(item, "value") or field(item, "right")
        expression = condition_expression(collector, value)
        if target is not None and target.type == "identifier" and expression.kind == "literal":
            result[collector._text(target)] = expression
    return result
