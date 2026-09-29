"""把 Python Tree-sitter AST 降级为公共结构化控制 IR。"""

from __future__ import annotations

import ast
from typing import Any

from backend.app.services.syntax_analysis import extensions_for_language
from backend.app.services.syntax_analysis import field as _field

from ..contracts import ValueTransferKind
from ..control_ir import ControlCallSite, ControlStatement, ControlValueTransfer
from .access_paths import (
    AccessPath,
    normalize_static_index,
    render_access_path,
)
from .treesitter import (
    FunctionDescriptor,
    TreeSitterFunctionCollector,
    TreeSitterProgramGraphFrontend,
)


class _PythonFunctionCollector(TreeSitterFunctionCollector):
    """保留 Python 参数、关键字调用、for 绑定和异常语义的适配器。"""

    language = "python"
    function_node_types = frozenset({"function_definition"})
    assignment_node_types = TreeSitterFunctionCollector.assignment_node_types | frozenset({
        "assignment", "augmented_assignment", "named_expression",
    })
    call_node_types = frozenset({"call"})
    loop_node_types = frozenset({"while_statement", "for_statement"})
    try_node_types = frozenset({"try_statement"})
    catch_node_types = frozenset({"except_clause"})
    throw_node_types = frozenset({"raise_statement"})
    switch_node_types = frozenset({"match_statement"})

    _SEQUENCE_TARGETS = frozenset({
        "list_pattern", "pattern_list", "tuple_pattern",
    })
    _SEQUENCE_VALUES = frozenset({
        "expression_list", "list", "list_expression", "tuple", "tuple_expression",
    })

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """将 Python 类声明加入依赖图一致的词法作用域。"""
        if node.type != "class_definition":
            return None
        name = self._text(_field(node, "name"))
        return scope + (name,) if name else scope

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """提取 Python 具名函数、参数和代码块。"""
        name = self._text(_field(node, "name"))
        parameters = self._python_parameters(_field(node, "parameters"))
        return FunctionDescriptor(
            name=name,
            scope=scope,
            parameters=tuple(parameters),
            # Python 允许运行时重复绑定函数，位置身份比注解签名更可靠。
            parameter_types=(),
            body=_field(node, "body"),
        ) if name else None

    def _python_parameters(self, node: Any | None) -> list[str]:
        """提取普通、默认、注解、位置限定和可变参数名称。"""
        if node is None:
            return []
        result: list[str] = []
        for item in node.named_children:
            if item.type in {"keyword_separator", "positional_separator"}:
                continue
            target = (
                _field(item, "name")
                or _field(item, "pattern")
                or _field(item, "left")
            )
            if item.type == "identifier":
                target = item
            names = self._identifiers(target or item)
            if names:
                result.append(names[0])
        return result

    def _condition(self, node: Any) -> Any | None:
        """Python for 的迭代表达式位于 right，其余控制结构使用公共字段。"""
        if node.type == "for_statement":
            return _field(node, "right")
        return super()._condition(node)

    def _loop_definitions(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 for 左侧绑定和迭代值使用。"""
        if node.type != "for_statement":
            return (), ()
        return (
            self._identifiers(_field(node, "left")),
            self._identifiers(_field(node, "right")),
        )

    def _call_arguments(
        self,
        arguments: Any | None,
    ) -> tuple[list[Any], list[tuple[str, Any]]]:
        """拆分 Python 位置参数和 keyword_argument。"""
        positional: list[Any] = []
        keywords: list[tuple[str, Any]] = []
        if arguments is None:
            return positional, keywords
        for item in arguments.named_children:
            if item.type != "keyword_argument":
                positional.append(item)
                continue
            name = self._text(_field(item, "name")) or "**"
            value = _field(item, "value")
            if value is not None:
                keywords.append((name, value))
        return positional, keywords

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """提取 Python 数据值身份，保留可确定的完整访问路径。"""
        if node is None:
            return ()
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "call":
                callee, arguments = self._call_parts(current)
                receiver, _ = self._split_callee(callee)
                result.extend(self._identifiers(receiver))
                positional, keywords = self._call_arguments(arguments)
                for argument in positional:
                    result.extend(self._identifiers(argument))
                for _, value in keywords:
                    result.extend(self._identifiers(value))
                continue
            if current.type == "keyword_argument":
                result.extend(self._identifiers(_field(current, "value")))
                continue
            if current.type in {"attribute", "subscript"}:
                access_path = render_access_path(self, current)
                if access_path:
                    result.append(access_path)
                    continue
                if current.type == "subscript":
                    self._limitations.append(
                        "Python 动态或复杂下标只保留保守词法使用，尚未解析元素别名。"
                    )
            if current.type == "identifier":
                literal = self._text(current)
                if literal:
                    result.append(literal)
                continue
            pending.extend(reversed(current.named_children))
        return self._unique(result)

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """解析 Python 属性和字符串/整数静态下标的词法路径。"""
        if node is None:
            return None
        if node.type == "identifier":
            root = self._text(node)
            return AccessPath(root) if root else None
        if node.type == "attribute":
            owner = self.resolve_access_path(_field(node, "object"))
            attribute = self._text(_field(node, "attribute"))
            return owner.attribute(attribute) if owner is not None and attribute else None
        if node.type != "subscript":
            return None
        owner = self.resolve_access_path(_field(node, "value"))
        index = self._static_subscript(_field(node, "subscript"))
        return owner.index(index) if owner is not None and index is not None else None

    def _static_subscript(self, node: Any | None) -> str | int | None:
        """安全解析 Python 字符串或整数常量下标，不执行项目代码。"""
        if node is None:
            return None
        try:
            value = ast.literal_eval(self._text(node))
        except (SyntaxError, ValueError):
            return None
        return normalize_static_index(value)

    def _lower_statement(
        self,
        node: Any,
        method_id: str,
    ) -> tuple[ControlStatement, ...]:
        """展开 with 主体；其余语句复用公共 Tree-sitter 降级。"""
        if node.type == "with_statement":
            body = _field(node, "body")
            header = next(
                (child for child in node.named_children if child != body),
                node,
            )
            return (
                self._leaf(
                    method_id,
                    "operation",
                    header,
                    definitions=self._with_definitions(header),
                    uses=self._identifiers(header),
                ),
            ) + self._lower_body(body, method_id)
        return super()._lower_statement(node, method_id)

    def _lower_handler(
        self,
        node: Any,
        method_id: str,
    ) -> tuple[ControlStatement, ...]:
        """在 except 入口定义 ``as error`` 别名，然后降低处理体。"""
        value = _field(node, "value")
        alias = _field(value, "alias")
        definitions = self._identifiers(alias)
        prefix: tuple[ControlStatement, ...] = ()
        if alias is not None and definitions:
            prefix = (self._leaf(
                method_id,
                "assignment",
                alias,
                definitions=definitions,
            ),)
        return prefix + self._lower_body(self._clause_body(node), method_id)

    def _with_definitions(self, node: Any) -> tuple[str, ...]:
        """提取 with_item 中 ``as`` 目标的词法名称。"""
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "as_pattern":
                result.extend(self._identifiers(_field(current, "alias")))
                continue
            pending.extend(reversed(current.named_children))
        return self._unique(result)

    def _value_transfers(
        self,
        node: Any,
        definitions: tuple[str, ...],
        uses: tuple[str, ...],
        calls: tuple[ControlCallSite, ...],
    ) -> tuple[ControlValueTransfer, ...]:
        """精确提取 Python 普通、解包及复合赋值的输入输出配对。"""
        transfers = self._assignment_value_transfers(node)
        return transfers or super()._value_transfers(
            node,
            definitions,
            uses,
            calls,
        )

    def _assignment_value_transfers(
        self,
        node: Any,
    ) -> tuple[ControlValueTransfer, ...]:
        """从 Python 赋值子树提取节点内传递；无法配对时保持保守完备。"""
        result: list[ControlValueTransfer] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type not in self.assignment_node_types:
                pending.extend(reversed(current.named_children))
                continue
            left = (
                _field(current, "left")
                or _field(current, "name")
                or _field(current, "argument")
                or _field(current, "pattern")
            )
            right = _field(current, "right") or _field(current, "value")
            targets = self._identifiers(left)
            if not targets or right is None:
                continue
            paired = self._paired_sequence_transfers(left, right)
            if paired:
                result.extend(paired)
                continue
            inputs = self._identifiers(right)
            is_augmented = current.type in {
                "augmented_assignment", "augmented_assignment_expression",
            }
            if is_augmented:
                inputs = self._unique((*targets, *inputs))
            calls = self._call_sites(right)
            transfer_kind = (
                "expression"
                if is_augmented else self._expression_transfer_kind(right, calls)
            )
            uncertain = transfer_kind == "call_result" or len(targets) > 1
            result.extend(
                ControlValueTransfer(
                    output_variable=target,
                    input_variables=inputs,
                    transfer_kind=transfer_kind,
                    callsite_ids=tuple(call.callsite_id for call in calls),
                    certainty="may" if uncertain else "must",
                )
                for target in targets
                if inputs
            )
        return tuple(dict.fromkeys(result))

    def _paired_sequence_transfers(
        self,
        left: Any | None,
        right: Any,
    ) -> tuple[ControlValueTransfer, ...]:
        """对等长的简单序列解包执行位置敏感配对。"""
        if (
            left is None
            or left.type not in self._SEQUENCE_TARGETS
            or right.type not in self._SEQUENCE_VALUES
        ):
            return ()
        left_items = list(left.named_children)
        right_items = list(right.named_children)
        if len(left_items) != len(right_items) or not left_items:
            return ()
        result: list[ControlValueTransfer] = []
        for target_node, value_node in zip(left_items, right_items):
            targets = self._identifiers(target_node)
            inputs = self._identifiers(value_node)
            if len(targets) != 1:
                return ()
            calls = self._call_sites(value_node)
            result.append(ControlValueTransfer(
                output_variable=targets[0],
                input_variables=inputs,
                transfer_kind=self._expression_transfer_kind(value_node, calls),
                callsite_ids=tuple(call.callsite_id for call in calls),
                certainty="may" if calls else "must",
            ))
        return tuple(result)

    def _expression_transfer_kind(
        self,
        node: Any,
        calls: tuple[ControlCallSite, ...],
    ) -> ValueTransferKind:
        """按 Python 右值形态标记简单赋值、表达式或调用结果。"""
        if calls:
            return "call_result"
        return "assignment" if node.type == "identifier" else "expression"


class PythonProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """在所有语言共享的 Tree-sitter 模板上实现 Python 适配。"""

    language = "python"
    parser_name = "python"
    extensions = extensions_for_language(language)
    collector_type = _PythonFunctionCollector
