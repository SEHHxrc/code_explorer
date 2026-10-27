"""字面量打包序列的纯语法适配；Java/Go 共用值绑定契约。"""

from typing import Any
from collections.abc import Callable

from backend.app.services.value_binding import BindingValue, ValueMember

from .tree_parser import field, semantic_children


def static_sequence_value(
    node: Any, identifiers: Callable[[Any], tuple[str, ...]]
) -> BindingValue:
    """输入表达式及词法读取器，返回直接数组/切片字面量成员或不透明值。

    仅展开 Java array initializer 与 Go 无键 literal value；不求值、追踪别名或猜测长度。
    """
    if node.type == "variadic_argument":
        node = next(iter(semantic_children(node)), node)
    body = (
        field(node, "value")
        if node.type == "array_creation_expression"
        else field(node, "body")
        if node.type == "composite_literal"
        else None
    )
    if body is None:
        body = next(
            (
                item
                for item in semantic_children(node)
                if item.type in {"array_initializer", "literal_value"}
            ),
            None,
        )
    if (
        body is None
        or node.type not in {"array_creation_expression", "composite_literal"}
        or any(item.type == "keyed_element" for item in semantic_children(body))
    ):
        return BindingValue(variables=identifiers(node))
    return BindingValue(
        kind="array",
        members=tuple(
            ValueMember(index, BindingValue(variables=identifiers(item)))
            for index, item in enumerate(semantic_children(body))
        ),
    )
