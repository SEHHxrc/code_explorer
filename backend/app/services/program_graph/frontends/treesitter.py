"""Tree-sitter 语言共享的函数收集与控制 IR 降级基础。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.value_binding import BindingPattern, BindingValue
from backend.app.services.value_binding.parameters import ParameterKind
from backend.app.services.syntax_analysis.javascript import parser_for_file
from backend.app.services.syntax_analysis.source_units import SourceUnit, source_unit
from backend.app.services.syntax_analysis import (
    DEFAULT_TREE_SITTER_PARSER_POOL,
    TreeSitterParserPool,
)
from backend.app.services.syntax_analysis import (
    field as _field,
)
from backend.app.services.syntax_analysis import (
    text as _text,
)

from ..control_ir import (
    ControlCallSite,
    ControlFunction,
    ControlStatement,
    ControlStatementKind,
    ControlValueTransfer,
)
from .access_paths import (
    AccessPath,
    parse_static_index_literal,
    render_access_path,
)
from .base import FrontendFileResult, ProgramGraphFrontend
from .common import location_from_tree_sitter, statement_id


@dataclass(frozen=True)
class FunctionDescriptor:
    """语言适配器从函数声明中提取的公共身份信息。"""

    name: str
    scope: tuple[str, ...]
    parameters: tuple[str, ...]
    parameter_types: tuple[str, ...]
    body: Any | None
    parameter_patterns: tuple[BindingPattern, ...] = ()
    parameter_kinds: dict[str, ParameterKind] = field(default_factory=dict)


class TreeSitterProgramGraphFrontend(ProgramGraphFrontend):
    """在统一前端模板上复用 Tree-sitter 解析和函数收集。"""

    language: str
    parser_name: str
    extensions: frozenset[str]
    collector_type: type[TreeSitterFunctionCollector]

    def __init__(self, parser_pool: TreeSitterParserPool | None = None) -> None:
        """允许依赖注入解析器池；默认在前端实例内按线程复用。"""
        self.parser_pool = parser_pool or DEFAULT_TREE_SITTER_PARSER_POOL

    def _analyze_file(
        self,
        project_root: Path,
        relative_path: str,
    ) -> FrontendFileResult:
        """解析一个 Tree-sitter 文件并返回函数和恢复性语法诊断。"""
        source = (project_root / relative_path).read_bytes()
        unit = source_unit(relative_path, source, self.language)
        source = unit.source
        tree = self.parser_pool.get(parser_for_file(self.parser_name, relative_path)).parse(source)
        failures = []
        if tree.root_node.has_error:
            failures.append({
                "path": relative_path,
                "language": self.language,
                "reason": "partial_parse_error",
            })
        collector = self.collector_type(relative_path, source)
        collector.collect(tree.root_node)
        collector.collect_unit(unit, tree.root_node)
        failures.extend({"path": relative_path, "language": self.language, "reason": reason} for reason in unit.diagnostics)
        return FrontendFileResult(
            functions=collector.functions,
            failures=failures,
        )


class TreeSitterFunctionCollector:
    """复用函数遍历、控制结构、调用点与词法 Def/Use 的公共实现。"""

    language = ""
    function_node_types: frozenset[str] = frozenset()
    block_node_types = frozenset({
        "block", "statement_block", "compound_statement", "constructor_body",
    })
    transparent_body_types = frozenset({"statement_list", "else_clause"})
    if_node_types = frozenset({"if_statement", "if_expression"})
    loop_node_types = frozenset({
        "while_statement", "for_statement", "do_statement", "for_in_statement",
        "for_of_statement", "while_expression", "for_expression", "loop_expression",
    })
    try_node_types = frozenset({"try_statement", "try_expression"})
    catch_node_types = frozenset({"catch_clause", "catch_block"})
    return_node_types = frozenset({"return_statement", "return_expression"})
    throw_node_types = frozenset({"throw_statement", "throw_expression", "panic_statement"})
    break_node_types = frozenset({"break_statement", "break_expression"})
    continue_node_types = frozenset({"continue_statement", "continue_expression"})
    declaration_node_types: frozenset[str] = frozenset()
    assignment_node_types = frozenset({
        "assignment_expression", "assignment_statement", "short_var_declaration",
        "compound_assignment_expr", "augmented_assignment_expression",
        "update_expression", "inc_statement", "dec_statement",
    })
    call_node_types = frozenset({"call_expression"})
    switch_node_types = frozenset({
        "switch_statement", "switch_expression", "match_expression",
        "match_statement", "select_statement",
    })
    identifier_node_types = frozenset({"identifier"})
    member_access_node_types: frozenset[str] = frozenset()
    index_access_node_types: frozenset[str] = frozenset()
    self_node_types: frozenset[str] = frozenset()
    value_sequence_node_types: frozenset[str] = frozenset()

    def __init__(self, path: str, source: bytes) -> None:
        """保存规范路径、源码字节和收集结果。"""
        self.path = ProgramIdentity.file_id(path)
        self.source = source
        self.functions: list[ControlFunction] = []
        self._limitations: list[str] = []

    def collect(self, root: Any) -> None:
        """从语法树根递归收集语言适配器识别的函数。"""
        self._walk_declarations(root, ())

    def collect_unit(self, unit: SourceUnit, root: Any) -> None:
        """混合文件可选入口；普通语言无需实现模板语义。"""
        return None

    def _walk_declarations(self, node: Any, scope: tuple[str, ...]) -> None:
        """维护词法作用域并避免把函数体中的语句误当作外层声明。"""
        scoped = self._scope_for_node(node, scope)
        if scoped is not None:
            for child in node.named_children:
                self._walk_declarations(child, scoped)
            return
        if node.type in self.function_node_types:
            descriptor = self._function_descriptor(node, scope)
            if descriptor is not None and descriptor.body is not None:
                self._append_function(node, descriptor)
                self._walk_nested_functions(
                    descriptor.body,
                    descriptor.scope + (descriptor.name,),
                )
            return
        for child in node.named_children:
            self._walk_declarations(child, scope)

    def _walk_nested_functions(self, node: Any, scope: tuple[str, ...]) -> None:
        """只收集函数体中显式具名的嵌套函数。"""
        for child in node.named_children:
            if child.type in self.function_node_types:
                descriptor = self._function_descriptor(child, scope)
                if descriptor is not None and descriptor.body is not None:
                    self._append_function(child, descriptor)
                    self._walk_nested_functions(
                        descriptor.body,
                        descriptor.scope + (descriptor.name,),
                    )
                continue
            self._walk_nested_functions(child, scope)

    def _append_function(self, node: Any, descriptor: FunctionDescriptor) -> None:
        """把语言函数描述转换为共享 ControlFunction。"""
        symbol_id = ProgramIdentity.symbol_id(
            self.path,
            descriptor.scope + (descriptor.name,),
        )
        location = location_from_tree_sitter(self.path, node)
        signature = ",".join(descriptor.parameter_types)
        method_id = (
            f"{symbol_id}({signature})"
            if descriptor.parameter_types
            else symbol_id + "@" + location.location_id.split(":", 1)[-1]
        )
        self._limitations = [
            f"{self.language} 的变量 uses 当前按词法标识符或访问路径近似；字段语义、指针和别名需由后续 Overlay 解析。"
        ]
        body = self._lower_body(descriptor.body, method_id)
        self.functions.append(ControlFunction(
            method_id=method_id,
            symbol_id=symbol_id,
            language=self.language,
            name=descriptor.name,
            location=location,
            parameters=descriptor.parameters,
            parameter_patterns=descriptor.parameter_patterns,
            parameter_kinds=descriptor.parameter_kinds,
            body=body,
            limitations=tuple(dict.fromkeys(self._limitations)),
        ))

    def _lower_body(self, node: Any | None, method_id: str) -> tuple[ControlStatement, ...]:
        """把块、语句列表或单条语句降低为公共控制语句。"""
        if node is None:
            return ()
        if node.type in self.block_node_types | self.transparent_body_types:
            children = node.named_children
        else:
            children = [node]
        result: list[ControlStatement] = []
        for child in children:
            if child.type in self.transparent_body_types:
                result.extend(self._lower_body(child, method_id))
            else:
                result.extend(self._lower_statement(child, method_id))
        return tuple(result)

    def _lower_statement(self, node: Any, method_id: str) -> tuple[ControlStatement, ...]:
        """将常见 Tree-sitter 控制节点降低为共享语句种类。"""
        node = self._unwrap_expression_statement(node)
        if node.type in self.block_node_types | self.transparent_body_types:
            return self._lower_body(node, method_id)
        location = location_from_tree_sitter(self.path, node)
        code = self._text(node)
        if node.type in self.if_node_types:
            condition = self._condition(node)
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "if"),
                kind="if",
                location=location,
                code=code,
                uses=self._identifiers(condition),
                calls=self._call_sites(condition),
                body=self._lower_body(self._consequence(node), method_id),
                alternative=self._lower_body(self._alternative(node), method_id),
            ),)
        if node.type in self.loop_node_types:
            condition = self._condition(node)
            definitions, declaration_uses = self._loop_definitions(node)
            if node.type in {"for_statement", "do_statement", "for_expression"}:
                self._limitations.append(
                    f"{self.language} 的 {node.type} 初始化、更新或后置条件采用保守循环近似。"
                )
            uses = tuple(dict.fromkeys((*declaration_uses, *self._identifiers(condition))))
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "loop"),
                kind="loop",
                location=location,
                code=code,
                definitions=definitions,
                uses=uses,
                calls=self._call_sites(condition or node),
                body=self._lower_body(self._body(node), method_id),
                alternative=self._lower_body(self._alternative(node), method_id),
            ),)
        if node.type in self.try_node_types:
            finalizer = self._child_of_type(node, {"finally_clause", "finally_block"})
            if finalizer is not None:
                self._limitations.append(
                    f"{self.language} try/finally 对突然退出的重放采用保守近似。"
                )
            handlers = tuple(
                self._lower_handler(child, method_id)
                for child in node.named_children if child.type in self.catch_node_types
            )
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "try"),
                kind="try",
                location=location,
                code=code,
                body=self._lower_body(self._body(node), method_id),
                handlers=handlers,
                finalizer=self._lower_body(self._clause_body(finalizer), method_id),
            ),)
        if node.type in self.return_node_types:
            return (self._leaf(method_id, "return", node, uses=self._identifiers(node)),)
        if node.type in self.throw_node_types:
            return (self._leaf(method_id, "throw", node, uses=self._identifiers(node)),)
        if node.type in self.break_node_types:
            return (self._leaf(method_id, "break", node),)
        if node.type in self.continue_node_types:
            return (self._leaf(method_id, "continue", node),)
        if node.type in self.declaration_node_types:
            definitions, uses = self._declaration_data(node)
            return (self._leaf(
                method_id,
                "assignment" if definitions else "operation",
                node,
                definitions=definitions,
                uses=uses,
            ),)
        definitions, uses = self._assignment_data(node)
        if definitions:
            return (self._leaf(
                method_id,
                "assignment",
                node,
                definitions=definitions,
                uses=uses,
            ),)
        if node.type in self.switch_node_types:
            self._limitations.append(
                f"{self.language} 的 {node.type} 尚未展开分支控制边。"
            )
        kind = "call" if self._contains_types(node, self.call_node_types) else "operation"
        return (self._leaf(method_id, kind, node, uses=self._identifiers(node)),)

    def _leaf(
        self,
        method_id: str,
        kind: ControlStatementKind,
        node: Any,
        *,
        definitions: tuple[str, ...] = (),
        uses: tuple[str, ...] = (),
    ) -> ControlStatement:
        """构造一个无嵌套控制体的操作节点。"""
        location = location_from_tree_sitter(self.path, node)
        calls = self._call_sites(node)
        return ControlStatement(
            statement_id=statement_id(method_id, location, kind),
            kind=kind,
            location=location,
            code=self._text(node),
            definitions=definitions,
            uses=uses,
            calls=calls,
            return_values=self._return_values(node) if kind == "return" else (),
            value_transfers=self._value_transfers(
                node,
                definitions,
                uses,
                calls,
            ),
        )

    def _value_transfers(
        self,
        node: Any,
        definitions: tuple[str, ...],
        uses: tuple[str, ...],
        calls: tuple[ControlCallSite, ...],
    ) -> tuple[ControlValueTransfer, ...]:
        """优先生成语法配对值流，无法配对时再保守连接输入输出。"""
        precise = self._structured_value_transfers(node)
        if precise:
            return precise
        if not definitions or not uses:
            return ()
        if calls:
            transfer_kind = "call_result"
        elif len(definitions) == 1 and len(uses) == 1:
            transfer_kind = "assignment"
        else:
            transfer_kind = "expression"
        uncertain = transfer_kind == "call_result" or len(definitions) > 1
        callsite_ids = tuple(call.callsite_id for call in calls)
        return tuple(
            ControlValueTransfer(
                output_variable=output,
                input_variables=uses,
                transfer_kind=transfer_kind,
                callsite_ids=callsite_ids,
                certainty="may" if uncertain else "must",
                provenance="inferred",
            )
            for output in definitions
        )

    def _structured_value_transfers(
        self,
        node: Any,
    ) -> tuple[ControlValueTransfer, ...]:
        """提取声明绑定和赋值表达式中可由语法确定的输入输出配对。"""
        result: list[ControlValueTransfer] = []
        for target, value in self._declaration_bindings(node):
            result.extend(self._binding_transfers(target, value, compound=False))
        pending = [node]
        while pending:
            current = pending.pop()
            if self._is_value_scope_boundary(current):
                continue
            if current.type not in self.assignment_node_types:
                pending.extend(reversed(current.named_children))
                continue
            left = (
                _field(current, "left")
                or _field(current, "argument")
                or _field(current, "pattern")
            )
            right = _field(current, "right") or _field(current, "value")
            if right is None:
                target = left or next(iter(current.named_children), None)
                result.extend(self._binding_transfers(target, target, compound=True))
                continue
            pairs = self._paired_bindings(left, right)
            compound = self._is_compound_assignment_node(current, left, right)
            for target, value in pairs:
                result.extend(self._binding_transfers(target, value, compound=compound))
        return tuple(dict.fromkeys(result))

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """返回声明中的目标和值节点；具体语言按声明语法覆盖。"""
        return ()

    def _paired_bindings(
        self,
        left: Any | None,
        right: Any,
    ) -> tuple[tuple[Any, Any], ...]:
        """对等长的并行表达式按位置配对，否则保留保守整体映射。"""
        if left is None:
            return ()
        left_items = self._sequence_items(left)
        right_items = self._sequence_items(right)
        is_explicit_sequence = (
            left.type in self.value_sequence_node_types
            or right.type in self.value_sequence_node_types
        )
        if is_explicit_sequence and len(left_items) == len(right_items) and left_items:
            return tuple(zip(left_items, right_items))
        return ((left, right),)

    def _sequence_items(self, node: Any) -> tuple[Any, ...]:
        """返回语言声明的并行值容器元素。"""
        if node.type not in self.value_sequence_node_types:
            return (node,)
        return tuple(node.named_children)

    def _binding_transfers(
        self,
        target: Any | None,
        value: Any | None,
        *,
        compound: bool,
    ) -> tuple[ControlValueTransfer, ...]:
        """把一对目标和值节点转换为变量感知的值传递。"""
        targets = self._identifiers(target)
        inputs = self._identifiers(value)
        if compound:
            inputs = self._unique((*targets, *inputs))
        if not targets or not inputs or value is None:
            return ()
        calls = self._call_sites(value)
        if calls:
            transfer_kind = "call_result"
        elif render_access_path(self, value) or value.type in self.identifier_node_types:
            transfer_kind = "assignment"
        else:
            transfer_kind = "expression"
        return tuple(
            ControlValueTransfer(
                output_variable=target_name,
                input_variables=inputs,
                transfer_kind=transfer_kind,
                callsite_ids=tuple(call.callsite_id for call in calls),
                certainty="may" if calls or len(targets) > 1 else "must",
            )
            for target_name in targets
        )

    def _is_compound_assignment_node(
        self,
        node: Any,
        left: Any | None,
        right: Any | None,
    ) -> bool:
        """根据节点类型和操作数之间的源码识别复合或更新赋值。"""
        if node.type in {
            "update_expression", "inc_statement", "dec_statement",
            "compound_assignment_expr", "augmented_assignment",
            "augmented_assignment_expression",
        }:
            return True
        if left is None or right is None:
            return False
        operator = self.source[left.end_byte:right.start_byte].decode(
            "utf-8", errors="replace",
        ).strip()
        return operator not in {"", "=", ":="}

    def _call_sites(self, node: Any | None) -> tuple[ControlCallSite, ...]:
        """递归提取调用点、实参和接收者，并与依赖边共享位置身份。"""
        if node is None:
            return ()
        result: list[ControlCallSite] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if self._is_value_scope_boundary(current):
                continue
            if current.type in self.call_node_types:
                callee, arguments = self._call_parts(current)
                positional, keywords = self._call_arguments(arguments)
                receiver_node, name = self._split_callee(callee)
                location = location_from_tree_sitter(self.path, current)
                result.append(ControlCallSite(
                    callsite_id=ProgramIdentity.callsite_id(
                        self.path,
                        location.line,
                        location.column,
                        location.end_line,
                        location.end_column,
                    ),
                    name=name,
                    receiver=self._text(receiver_node),
                    location=location,
                    positional_arguments=tuple(
                        self._identifiers(argument)
                        for argument in positional
                    ),
                    keyword_arguments=tuple(
                        (keyword, self._identifiers(value))
                        for keyword, value in keywords
                    ),
                    receiver_identifiers=self._identifiers(receiver_node),
                    argument_values=self._call_values(positional),
                    positional_spread_positions=tuple(
                        index for index, argument in enumerate(positional)
                        if argument.type == "variadic_argument"
                    ),
                    result_targets=self._call_result_targets(current),
                    positional_argument_locations=tuple(location_from_tree_sitter(self.path, item) for item in positional),
                    keyword_argument_locations=tuple((name, location_from_tree_sitter(self.path, item)) for name, item in keywords),
                ))
            pending.extend(reversed(current.named_children))
        return tuple(sorted(result, key=lambda item: (
            item.location.line,
            item.location.column,
            item.callsite_id,
        )))

    def _call_values(self, arguments: list[Any]) -> tuple[BindingValue, ...]:
        """可选结构化实参协议；尚未适配的语言保持原来的位置参数行为。"""
        return ()

    def _assignment_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取语句子树中的赋值目标与右值词法使用。"""
        definitions: list[str] = []
        uses: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if self._is_value_scope_boundary(current):
                continue
            if current.type in self.assignment_node_types:
                left = (
                    _field(current, "left")
                    or _field(current, "argument")
                    or _field(current, "pattern")
                )
                right = _field(current, "right") or _field(current, "value")
                targets = self._identifiers(left)
                definitions.extend(targets)
                uses.extend(self._identifiers(right))
                if current.type in {
                    "update_expression", "inc_statement", "dec_statement",
                    "compound_assignment_expr", "augmented_assignment",
                    "augmented_assignment_expression",
                }:
                    uses.extend(targets)
                continue
            pending.extend(reversed(current.named_children))
        return self._unique(definitions), self._unique(uses)

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """由语言适配器提取声明定义；默认退化为赋值表达式分析。"""
        return self._assignment_data(node)

    def _loop_definitions(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 foreach/range 循环变量；默认不声明新变量。"""
        return (), ()

    def _condition(self, node: Any) -> Any | None:
        """返回条件字段，必要时从非 body 子节点中保守选择。"""
        condition = _field(node, "condition") or _field(node, "value")
        if condition is not None:
            return condition
        body = self._body(node)
        return next(
            (child for child in node.named_children if child != body),
            None,
        )

    @staticmethod
    def _consequence(node: Any) -> Any | None:
        """返回 if 的真分支。"""
        return _field(node, "consequence") or _field(node, "body")

    @staticmethod
    def _alternative(node: Any) -> Any | None:
        """返回 if/loop 的替代分支并保留 else-if。"""
        alternative = _field(node, "alternative")
        if alternative is None:
            return None
        if alternative.type == "else_clause" and len(alternative.named_children) == 1:
            return alternative.named_children[0]
        return alternative

    @staticmethod
    def _body(node: Any) -> Any | None:
        """返回复合语句主体。"""
        return _field(node, "body") or _field(node, "consequence")

    def _unwrap_expression_statement(self, node: Any) -> Any:
        """展开只包裹一个控制表达式的表达式语句。"""
        if node.type != "expression_statement" or len(node.named_children) != 1:
            return node
        child = node.named_children[0]
        control_types = (
            self.if_node_types
            | self.loop_node_types
            | self.try_node_types
            | self.return_node_types
            | self.throw_node_types
            | self.break_node_types
            | self.continue_node_types
        )
        return child if child.type in control_types else node

    def _lower_handler(
        self,
        node: Any,
        method_id: str,
    ) -> tuple[ControlStatement, ...]:
        """在 catch 入口定义异常参数，再降低处理体。"""
        parameter = _field(node, "parameter")
        body = self._clause_body(node)
        definitions = self._identifiers(parameter)
        prefix: tuple[ControlStatement, ...] = ()
        if parameter is not None and definitions:
            prefix = (self._leaf(
                method_id,
                "assignment",
                parameter,
                definitions=definitions,
            ),)
        return prefix + self._lower_body(body, method_id)

    @staticmethod
    def _clause_body(node: Any | None) -> Any | None:
        """返回 catch/finally 包装中的实际代码块。"""
        if node is None:
            return None
        body = _field(node, "body")
        if body is not None:
            return body
        return next(
            (
                child for child in node.named_children
                if child.type in {
                    "block", "statement_block", "compound_statement",
                }
            ),
            node,
        )

    def _call_parts(self, node: Any) -> tuple[Any | None, Any | None]:
        """返回调用目标和参数容器。"""
        return (
            _field(node, "function") or _field(node, "constructor") or _field(node, "macro"),
            _field(node, "arguments") or _field(node, "argument"),
        )

    def _split_callee(self, callee: Any | None) -> tuple[Any | None, str]:
        """把成员调用拆成接收者和方法名称。"""
        if callee is None:
            return None, ""
        member_types = {
            "member_expression", "selector_expression", "field_expression",
            "qualified_identifier", "scoped_identifier", "attribute",
        }
        if callee.type not in member_types:
            return None, self._text(callee)
        receiver = (
            _field(callee, "object")
            or _field(callee, "operand")
            or _field(callee, "argument")
            or _field(callee, "value")
            or _field(callee, "scope")
            or _field(callee, "path")
        )
        name_node = (
            _field(callee, "property")
            or _field(callee, "field")
            or _field(callee, "name")
            or _field(callee, "attribute")
        )
        return receiver, self._text(name_node) or self._text(callee)

    @staticmethod
    def _argument_nodes(arguments: Any | None) -> list[Any]:
        """返回调用参数容器中的具名参数节点。"""
        return list(arguments.named_children) if arguments is not None else []

    def _call_result_targets(self, node: Any) -> tuple[str, ...]:
        """可选的有序多返回值接收槽位；普通语言默认不声明 tuple ABI。"""
        return ()

    def _return_values(self, node: Any) -> tuple[tuple[str, ...], ...]:
        """可选的各返回分量使用；默认单返回值兼容原图协议。"""
        return ()

    def _call_arguments(
        self,
        arguments: Any | None,
    ) -> tuple[list[Any], list[tuple[str, Any]]]:
        """拆分位置和关键字实参；默认语言只有位置参数。"""
        return self._argument_nodes(arguments), []

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """提取运行时值身份，排除被调用名称并保留精确访问路径。"""
        if node is None:
            return ()
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if self._is_value_scope_boundary(current):
                continue
            if current.type in self.call_node_types:
                callee, arguments = self._call_parts(current)
                receiver, _ = self._split_callee(callee)
                result.extend(self._identifiers(receiver))
                positional, keywords = self._call_arguments(arguments)
                for argument in positional:
                    result.extend(self._identifiers(argument))
                for _, value in keywords:
                    result.extend(self._identifiers(value))
                continue
            if current.type in self.member_access_node_types | self.index_access_node_types:
                access_path = render_access_path(self, current)
                if access_path:
                    result.append(access_path)
                    continue
                if current.type in self.index_access_node_types:
                    self._limitations.append(
                        f"{self.language} 动态或复杂下标只保留保守词法使用，尚未解析元素别名。"
                    )
            if current.type in self.identifier_node_types:
                literal = self._text(current)
                if literal:
                    result.append(literal)
                continue
            pending.extend(reversed(current.named_children))
        return self._unique(result)

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """按公共 AST 字段约定解析字段链和静态下标链。"""
        if node is None:
            return None
        if node.type in self.identifier_node_types:
            root = self._text(node)
            return AccessPath(root) if root else None
        if node.type in self.self_node_types:
            root = self._text(node)
            return AccessPath(root) if root else None
        if node.type in self.member_access_node_types:
            owner_node, member_node = self._member_access_parts(node)
            owner = self.resolve_access_path(owner_node)
            member = self._text(member_node)
            return owner.attribute(member) if owner is not None and member else None
        if node.type not in self.index_access_node_types:
            return None
        owner_node, index_node = self._index_access_parts(node)
        owner = self.resolve_access_path(owner_node)
        index = self._static_index(index_node)
        return owner.index(index) if owner is not None and index is not None else None

    def _member_access_parts(self, node: Any) -> tuple[Any | None, Any | None]:
        """返回常见成员访问节点的拥有者和字段节点。"""
        owner = (
            _field(node, "object")
            or _field(node, "operand")
            or _field(node, "argument")
            or _field(node, "value")
        )
        member = (
            _field(node, "property")
            or _field(node, "field")
            or _field(node, "attribute")
            or _field(node, "name")
        )
        return owner, member

    def _index_access_parts(self, node: Any) -> tuple[Any | None, Any | None]:
        """返回常见下标节点的拥有者和单一下标表达式。"""
        owner = (
            _field(node, "object")
            or _field(node, "operand")
            or _field(node, "argument")
            or _field(node, "value")
            or _field(node, "array")
        )
        index = _field(node, "index") or _field(node, "subscript") or _field(node, "indices")
        if index is not None and len(index.named_children) == 1 and index.type in {
            "subscript_argument_list", "argument_list",
        }:
            index = index.named_children[0]
        if owner is None and len(node.named_children) >= 2:
            owner = node.named_children[0]
            index = node.named_children[1]
        return owner, index

    def _static_index(self, node: Any | None) -> str | int | None:
        """解析跨语言常见的字符串和整数静态下标。"""
        return parse_static_index_literal(self._text(node)) if node is not None else None

    def _is_value_scope_boundary(self, node: Any) -> bool:
        """嵌套函数的体操作不能进入外层值/调用收集；普通 declarator 不是边界。"""
        if node.type not in self.function_node_types:
            return False
        if node.type == "variable_declarator":
            value = _field(node, "value")
            return value is not None and value.type in {"arrow_function", "function_expression"}
        return True

    def _contains_types(self, node: Any, node_types: frozenset[str]) -> bool:
        """判断当前值作用域是否包含节点；函数创建不执行其函数体。"""
        pending = [node]
        while pending:
            current = pending.pop()
            if self._is_value_scope_boundary(current):
                continue
            if current.type in node_types:
                return True
            pending.extend(current.named_children)
        return False

    @staticmethod
    def _child_of_type(node: Any, node_types: set[str]) -> Any | None:
        """返回第一个指定种类的直接具名子节点。"""
        return next((child for child in node.named_children if child.type in node_types), None)

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        """按首次出现顺序去重非空字符串。"""
        return tuple(dict.fromkeys(value for value in values if value))

    def _text(self, node: Any | None) -> str:
        """读取一个 Tree-sitter 节点的 UTF-8 源码。"""
        return _text(node, self.source)

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """语言适配器可返回该节点内部的新词法作用域。"""
        return None

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """语言适配器必须描述函数名称、参数、主体和归属作用域。"""
        raise NotImplementedError
