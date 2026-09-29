"""把 Java Tree-sitter AST 降级为公共结构化控制 IR。"""

from __future__ import annotations

import ast
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import (
    extensions_for_language,
)
from backend.app.services.syntax_analysis import (
    field as _field,
)
from backend.app.services.syntax_analysis import (
    fields as _fields,
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
from ..contracts import ValueTransferKind
from .access_paths import (
    AccessPath,
    normalize_static_index,
    render_access_path,
)
from .common import location_from_tree_sitter, statement_id
from .treesitter import TreeSitterFunctionCollector, TreeSitterProgramGraphFrontend

_CLASS_NODES = {
    "class_declaration", "interface_declaration", "enum_declaration",
    "record_declaration", "annotation_type_declaration",
}
_METHOD_NODES = {"method_declaration", "constructor_declaration"}


class _JavaFunctionCollector(TreeSitterFunctionCollector):
    """收集 Java 方法，并把方法体降低到公共控制语句。"""

    def __init__(self, path: str, source: bytes) -> None:
        """保存文件身份和源码字节。"""
        self.path = path
        self.source = source
        self.functions: list[ControlFunction] = []
        self._limitations: list[str] = []

    def collect(self, root: Any) -> None:
        """从语法树根开始收集具名类型中的方法和构造器。"""
        self._walk_declarations(root, ())

    def _walk_declarations(self, node: Any, scope: tuple[str, ...]) -> None:
        """递归类作用域；方法体内部的局部类暂不提升为项目方法。"""
        if node.type in _CLASS_NODES:
            name = self._text(_field(node, "name")) or "<anonymous>"
            for child in node.named_children:
                self._walk_declarations(child, scope + (name,))
            return
        if node.type in _METHOD_NODES:
            self._append_method(node, scope)
            return
        for child in node.named_children:
            self._walk_declarations(child, scope)

    def _append_method(self, node: Any, scope: tuple[str, ...]) -> None:
        """构造一个签名感知的 Java 方法控制 IR。"""
        name = self._text(_field(node, "name"))
        if not name:
            return
        parameter_nodes = self._parameter_nodes(_field(node, "parameters"))
        parameters = tuple(
            self._text(_field(item, "name")) for item in parameter_nodes
            if self._text(_field(item, "name"))
        )
        parameter_types = tuple(
            self._text(_field(item, "type")) or "?" for item in parameter_nodes
        )
        symbol_id = ProgramIdentity.symbol_id(self.path, scope + (name,))
        method_id = symbol_id + "(" + ",".join(parameter_types) + ")"
        body_node = _field(node, "body")
        self._limitations = [
            "Java 的变量 uses 当前按词法标识符或字段访问路径近似，别名解析将在语义 Overlay 补充。"
        ]
        body = self._lower_body(body_node, method_id)
        self.functions.append(ControlFunction(
            method_id=method_id,
            symbol_id=symbol_id,
            language="java",
            name=name,
            location=location_from_tree_sitter(self.path, node),
            parameters=parameters,
            body=body,
            limitations=tuple(dict.fromkeys(self._limitations)),
        ))

    def _lower_body(self, node: Any | None, method_id: str) -> tuple[ControlStatement, ...]:
        """降低代码块或单条语句。"""
        if node is None:
            return ()
        children = node.named_children if node.type in {"block", "constructor_body"} else [node]
        result: list[ControlStatement] = []
        for child in children:
            result.extend(self._lower_statement(child, method_id))
        return tuple(result)

    def _lower_statement(self, node: Any, method_id: str) -> tuple[ControlStatement, ...]:
        """把一条 Java 语句降级为公共控制语句。"""
        node_type = node.type
        location = location_from_tree_sitter(self.path, node)
        code = self._text(node)
        if node_type == "if_statement":
            condition = _field(node, "condition")
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "if"),
                kind="if",
                location=location,
                code=code,
                uses=self._identifiers(condition),
                calls=self._call_sites(condition),
                body=self._lower_body(_field(node, "consequence"), method_id),
                alternative=self._lower_body(_field(node, "alternative"), method_id),
            ),)
        if node_type in {
            "while_statement", "for_statement", "enhanced_for_statement", "do_statement",
        }:
            condition = _field(node, "condition") or _field(node, "value")
            definitions: tuple[str, ...] = ()
            if node_type == "enhanced_for_statement":
                name = self._text(_field(node, "name"))
                definitions = (name,) if name else ()
            if node_type in {"for_statement", "do_statement"}:
                self._limitations.append(
                    f"{node_type} 的初始化、更新或后置条件当前使用保守循环近似。"
                )
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "loop"),
                kind="loop",
                location=location,
                code=code,
                definitions=definitions,
                uses=self._identifiers(condition or node),
                calls=self._call_sites(condition or node),
                body=self._lower_body(_field(node, "body"), method_id),
            ),)
        if node_type == "try_statement":
            finalizer_node = next(
                (item for item in node.named_children if item.type == "finally_clause"),
                None,
            )
            if finalizer_node is not None:
                self._limitations.append(
                    "Java try/finally 对突然退出的 finally 重放仍为保守近似。"
                )
            handlers = tuple(
                self._lower_catch(item, method_id)
                for item in node.named_children if item.type == "catch_clause"
            )
            return (ControlStatement(
                statement_id=statement_id(method_id, location, "try"),
                kind="try",
                location=location,
                code=code,
                body=self._lower_body(_field(node, "body"), method_id),
                handlers=handlers,
                finalizer=self._lower_body(
                    next(iter(finalizer_node.named_children), None)
                    if finalizer_node is not None else None,
                    method_id,
                ),
            ),)
        if node_type == "return_statement":
            return (self._leaf(method_id, "return", node, uses=self._identifiers(node)),)
        if node_type == "throw_statement":
            return (self._leaf(method_id, "throw", node, uses=self._identifiers(node)),)
        if node_type == "break_statement":
            return (self._leaf(method_id, "break", node),)
        if node_type == "continue_statement":
            return (self._leaf(method_id, "continue", node),)
        if node_type == "local_variable_declaration":
            definitions = tuple(dict.fromkeys(
                self._text(_field(item, "name"))
                for item in _fields(node, "declarator")
                if self._text(_field(item, "name"))
            ))
            return (self._leaf(
                method_id,
                "assignment",
                node,
                definitions=definitions,
                uses=tuple(name for name in self._identifiers(node) if name not in definitions),
            ),)
        if node_type == "expression_statement":
            definitions = self._assignment_targets(node)
            has_call = self._contains_type(node, "method_invocation")
            kind = "assignment" if definitions else "call" if has_call else "operation"
            uses = (
                self._assignment_uses(node)
                if definitions else self._identifiers(node)
            )
            return (self._leaf(
                method_id, kind, node, definitions=definitions, uses=uses,
            ),)
        if node_type in {"synchronized_statement", "labeled_statement"}:
            header = self._leaf(method_id, "operation", node, uses=self._identifiers(node))
            body = self._lower_body(_field(node, "body"), method_id)
            return (header,) + body
        if node_type in {"switch_expression", "switch_statement"}:
            self._limitations.append("Java switch 尚未展开 case 控制边。")
        return (self._leaf(method_id, "operation", node, uses=self._identifiers(node)),)

    def _lower_catch(self, node: Any, method_id: str) -> tuple[ControlStatement, ...]:
        """在 catch 入口显式定义异常参数，然后降低处理体。"""
        parameter = next(
            (item for item in node.named_children if item.type == "catch_formal_parameter"),
            None,
        )
        body = _field(node, "body")
        if parameter is None:
            return self._lower_body(body, method_id)
        name = self._text(_field(parameter, "name"))
        entry = self._leaf(
            method_id,
            "assignment",
            parameter,
            definitions=(name,) if name else (),
        )
        return (entry,) + self._lower_body(body, method_id)

    def _leaf(
        self,
        method_id: str,
        kind: ControlStatementKind,
        node: Any,
        *,
        definitions: tuple[str, ...] = (),
        uses: tuple[str, ...] = (),
    ) -> ControlStatement:
        """构造无嵌套控制体的 Java 操作节点。"""
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
        """精确提取 Java 局部声明、赋值和更新表达式的传递关系。"""
        if node.type == "local_variable_declaration":
            transfers = self._local_declaration_transfers(node)
        else:
            transfers = self._assignment_value_transfers(node)
        return transfers or super()._value_transfers(
            node,
            definitions,
            uses,
            calls,
        )

    def _local_declaration_transfers(
        self,
        node: Any,
    ) -> tuple[ControlValueTransfer, ...]:
        """按 declarator 分离同一 Java 声明语句中的多个初始化值。"""
        result: list[ControlValueTransfer] = []
        for declarator in _fields(node, "declarator"):
            output = self._text(_field(declarator, "name"))
            value = _field(declarator, "value")
            inputs = self._identifiers(value)
            if not output or not inputs or value is None:
                continue
            calls = self._call_sites(value)
            result.append(ControlValueTransfer(
                output_variable=output,
                input_variables=inputs,
                transfer_kind=self._expression_transfer_kind(value, calls),
                callsite_ids=tuple(call.callsite_id for call in calls),
                certainty="may" if calls else "must",
            ))
        return tuple(result)

    def _assignment_value_transfers(
        self,
        node: Any,
    ) -> tuple[ControlValueTransfer, ...]:
        """按 Java 赋值表达式分别记录右值输入和左值输出。"""
        result: list[ControlValueTransfer] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "assignment_expression":
                left = _field(current, "left")
                right = _field(current, "right")
                targets = self._identifiers(left)
                inputs = self._identifiers(right)
                if self._is_compound_assignment(left, right):
                    inputs = tuple(dict.fromkeys((*targets, *inputs)))
                calls = self._call_sites(right)
                transfer_kind = self._expression_transfer_kind(right, calls)
                result.extend(
                    ControlValueTransfer(
                        output_variable=target,
                        input_variables=inputs,
                        transfer_kind=transfer_kind,
                        callsite_ids=tuple(call.callsite_id for call in calls),
                        certainty="may" if calls or len(targets) > 1 else "must",
                    )
                    for target in targets
                    if inputs
                )
                continue
            if current.type == "update_expression":
                targets = self._identifiers(current)
                result.extend(
                    ControlValueTransfer(
                        output_variable=target,
                        input_variables=(target,),
                        transfer_kind="expression",
                    )
                    for target in targets
                )
                continue
            pending.extend(reversed(current.named_children))
        return tuple(dict.fromkeys(result))

    def _is_compound_assignment(
        self,
        left: Any | None,
        right: Any | None,
    ) -> bool:
        """通过左右操作数之间的源码判断 Java 复合赋值运算符。"""
        if left is None or right is None:
            return False
        operator = self.source[left.end_byte:right.start_byte].decode(
            "utf-8",
            errors="replace",
        ).strip()
        return operator != "="

    @staticmethod
    def _expression_transfer_kind(
        node: Any | None,
        calls: tuple[ControlCallSite, ...],
    ) -> ValueTransferKind:
        """按 Java 右值形态标记简单赋值、表达式或调用结果。"""
        if calls:
            return "call_result"
        return "assignment" if node is not None and node.type == "identifier" else "expression"

    def _call_sites(self, node: Any | None) -> tuple[ControlCallSite, ...]:
        """提取 Java 方法调用和对象构造的调用点参数。"""
        if node is None:
            return ()
        result: list[ControlCallSite] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "method_invocation":
                name = self._text(_field(current, "name"))
                receiver_node = _field(current, "object")
                receiver = self._text(receiver_node)
                arguments = _field(current, "arguments")
                result.append(self._call_site(
                    current,
                    name=name,
                    receiver=receiver,
                    receiver_node=receiver_node,
                    arguments=arguments,
                ))
            elif current.type == "object_creation_expression":
                name = self._text(_field(current, "type"))
                arguments = _field(current, "arguments")
                result.append(self._call_site(
                    current,
                    name=name,
                    receiver="",
                    receiver_node=None,
                    arguments=arguments,
                ))
            pending.extend(reversed(current.named_children))
        return tuple(sorted(result, key=lambda item: (
            item.location.line, item.location.column, item.callsite_id,
        )))

    def _call_site(
        self,
        node: Any,
        *,
        name: str,
        receiver: str,
        receiver_node: Any | None,
        arguments: Any | None,
    ) -> ControlCallSite:
        """把一个 Java 调用表达式转换为公共调用点。"""
        location = location_from_tree_sitter(self.path, node)
        argument_nodes = list(arguments.named_children) if arguments is not None else []
        return ControlCallSite(
            callsite_id=ProgramIdentity.callsite_id(
                self.path,
                location.line,
                location.column,
                location.end_line,
                location.end_column,
            ),
            name=name,
            receiver=receiver,
            location=location,
            positional_arguments=tuple(
                self._identifiers(argument) for argument in argument_nodes
            ),
            receiver_identifiers=self._identifiers(receiver_node),
        )

    def _assignment_targets(self, node: Any) -> tuple[str, ...]:
        """提取赋值和自增/自减表达式的词法目标。"""
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "assignment_expression":
                left = _field(current, "left")
                result.extend(self._identifiers(left))
                continue
            if current.type == "update_expression":
                result.extend(self._identifiers(current))
                continue
            pending.extend(current.named_children)
        return tuple(dict.fromkeys(result))

    def _assignment_uses(self, node: Any) -> tuple[str, ...]:
        """提取赋值右值以及自增/自减目标的读取名字。"""
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "assignment_expression":
                result.extend(self._identifiers(_field(current, "right")))
                continue
            if current.type == "update_expression":
                result.extend(self._identifiers(current))
                continue
            pending.extend(current.named_children)
        return tuple(dict.fromkeys(result))

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """提取 Java 数据值身份，保留可确定的完整访问路径。"""
        if node is None:
            return ()
        result: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == "method_invocation":
                receiver = _field(current, "object")
                arguments = _field(current, "arguments")
                result.extend(self._identifiers(receiver))
                if arguments is not None:
                    for argument in arguments.named_children:
                        result.extend(self._identifiers(argument))
                continue
            if current.type == "object_creation_expression":
                arguments = _field(current, "arguments")
                if arguments is not None:
                    for argument in arguments.named_children:
                        result.extend(self._identifiers(argument))
                continue
            if current.type in {"field_access", "array_access"}:
                access_path = render_access_path(self, current)
                if access_path:
                    result.append(access_path)
                    continue
                if current.type == "array_access":
                    self._limitations.append(
                        "Java 动态或复杂数组下标只保留保守词法使用，尚未解析元素别名。"
                    )
            if current.type == "this":
                result.append("this")
                continue
            if current.type == "identifier":
                result.append(self._text(current))
                continue
            pending.extend(reversed(current.named_children))
        return tuple(dict.fromkeys(name for name in result if name))

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """解析 Java 字段和整数常量数组下标的词法路径。"""
        if node is None:
            return None
        if node.type == "identifier":
            root = self._text(node)
            return AccessPath(root) if root else None
        if node.type == "this":
            return AccessPath("this")
        if node.type == "field_access":
            owner = self.resolve_access_path(_field(node, "object"))
            field = self._text(_field(node, "field"))
            return owner.attribute(field) if owner is not None and field else None
        if node.type != "array_access":
            return None
        owner = self.resolve_access_path(_field(node, "array"))
        index = self._static_array_index(_field(node, "index"))
        return owner.index(index) if owner is not None and index is not None else None

    def _static_array_index(self, node: Any | None) -> int | None:
        """将 Java 整数或字符常量数组下标规范化为整数。"""
        if node is None:
            return None
        raw = self._text(node).strip()
        if node.type == "character_literal":
            try:
                value = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                return None
            return ord(value) if isinstance(value, str) and len(value) == 1 else None
        normalized = raw.rstrip("lL").replace("_", "")
        sign = -1 if normalized.startswith("-") else 1
        unsigned = normalized.lstrip("+-")
        try:
            if unsigned.lower().startswith(("0x", "0b")):
                value = int(unsigned, 0)
            elif len(unsigned) > 1 and unsigned.startswith("0"):
                value = int(unsigned, 8)
            else:
                value = int(unsigned, 10)
        except ValueError:
            return None
        index = normalize_static_index(sign * value)
        return index if isinstance(index, int) else None

    @staticmethod
    def _parameter_nodes(node: Any | None) -> list[Any]:
        """返回普通和可变参数节点。"""
        if node is None:
            return []
        return [
            child for child in node.named_children
            if child.type in {"formal_parameter", "spread_parameter"}
        ]

    @staticmethod
    def _contains_type(node: Any, node_type: str) -> bool:
        """判断子树是否包含指定 Tree-sitter 节点类型。"""
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == node_type:
                return True
            pending.extend(current.named_children)
        return False

    def _text(self, node: Any | None) -> str:
        """读取节点对应的 UTF-8 文本。"""
        return _text(node, self.source)


class JavaProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """在公共 Tree-sitter 模板上保留 Java 专用控制语义。"""

    language = "java"
    parser_name = "java"
    extensions = extensions_for_language(language)
    collector_type = _JavaFunctionCollector
