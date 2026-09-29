"""将 Java Tree-sitter AST 转换为规则无关的安全分析 IR。"""

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
    text as _text,
)

from ..ir import (
    IRCall,
    IRCondition,
    IRDecorator,
    IRExpression,
    IRFunction,
    IRLocation,
    IRParameter,
    SecurityProgramIR,
)
from .treesitter import TreeSitterSecurityFrontend

_CLASS_NODES = {
    "class_declaration", "interface_declaration", "enum_declaration",
    "record_declaration", "annotation_type_declaration",
}
_METHOD_NODES = {"method_declaration", "constructor_declaration"}
_CALL_NODES = {"method_invocation", "object_creation_expression"}
_ANNOTATION_NODES = {"annotation", "marker_annotation"}
_LITERAL_NODES = {
    "string_literal", "character_literal", "decimal_integer_literal",
    "hex_integer_literal", "octal_integer_literal", "binary_integer_literal",
    "decimal_floating_point_literal", "hex_floating_point_literal",
    "true", "false", "null_literal",
}


class JavaSecurityFrontend(TreeSitterSecurityFrontend):
    """提取 Java 方法、框架注解、调用、赋值目标和条件。"""

    language = "java"
    parser_name = "java"
    extensions = extensions_for_language(language)

    def collect_file(
        self,
        program: SecurityProgramIR,
        path: str,
        source: bytes,
        root: Any,
    ) -> None:
        """将一个 Java 文件的可观察语法事实追加到通用 IR。"""
        collector = _JavaIRCollector(path, source, root)
        collector.collect(root)
        program.functions.extend(collector.functions)
        program.calls.extend(collector.calls)
        program.conditions.extend(collector.conditions)


class _JavaIRCollector:
    """维护 Java 类型作用域，并使安全调用点与 ProgramGraph 身份一致。"""

    def __init__(self, path: str, source: bytes, root: Any) -> None:
        """预索引调用赋值目标，供 Source 返回值连接到 ProgramGraph 定义。"""
        self.path = ProgramIdentity.file_id(path)
        self.source = source
        self.functions: list[IRFunction] = []
        self.calls: list[IRCall] = []
        self.conditions: list[IRCondition] = []
        self._assigned_targets = self._collect_assigned_targets(root)

    def collect(self, root: Any) -> None:
        """从编译单元递归收集类型中的方法和构造器。"""
        self._walk_declarations(root, (), ())

    def _walk_declarations(
        self,
        node: Any,
        scope: tuple[str, ...],
        owner_types: tuple[str, ...],
    ) -> None:
        """维护具名类型作用域，避免把方法体重复归属到外层函数。"""
        if node.type in _CLASS_NODES:
            name = self._text(_field(node, "name")) or "<anonymous>"
            inherited = self._owner_types(node)
            for child in node.named_children:
                self._walk_declarations(child, scope + (name,), inherited)
            return
        if node.type in _METHOD_NODES:
            self._append_method(node, scope, owner_types)
            return
        for child in node.named_children:
            self._walk_declarations(child, scope, owner_types)

    def _append_method(
        self,
        node: Any,
        scope: tuple[str, ...],
        owner_types: tuple[str, ...],
    ) -> None:
        """构造 Java 函数 IR，再扫描方法体中的安全相关表达式。"""
        name = self._text(_field(node, "name"))
        if not name:
            return
        symbol = ProgramIdentity.symbol_id(self.path, scope + (name,))
        parameters = tuple(self._parameter(item) for item in self._parameter_nodes(node))
        self.functions.append(IRFunction(
            symbol=symbol,
            name=name,
            parameters=parameters,
            decorators=self._decorators(node),
            location=self._location(node),
            owner_types=owner_types,
        ))
        variable_types = {
            parameter.name: parameter.annotation
            for parameter in parameters
            if parameter.annotation
        }
        body = _field(node, "body")
        if body is not None:
            variable_types.update(self._local_variable_types(body))
            self._walk_body(body, symbol, variable_types)

    def _walk_body(
        self,
        node: Any,
        symbol: str,
        variable_types: dict[str, str],
    ) -> None:
        """一次遍历收集调用和条件；局部/匿名类型暂不混入外层方法。"""
        if node.type in _CLASS_NODES or node.type in _METHOD_NODES:
            return
        if node.type in _CALL_NODES:
            self.calls.append(self._call(node, symbol, variable_types))
        if node.type == "if_statement":
            condition = _field(node, "condition")
            if condition is not None:
                self.conditions.append(IRCondition(
                    kind="if",
                    symbol=symbol,
                    expression=self._expression(condition),
                    location=self._location(condition),
                ))
        elif node.type == "assert_statement":
            expression = next(iter(node.named_children), None)
            if expression is not None:
                self.conditions.append(IRCondition(
                    kind="assert",
                    symbol=symbol,
                    expression=self._expression(expression),
                    location=self._location(node),
                ))
        for child in node.named_children:
            self._walk_body(child, symbol, variable_types)

    def _call(
        self,
        node: Any,
        symbol: str,
        variable_types: dict[str, str],
    ) -> IRCall:
        """规范化 Java 方法调用或对象构造，并保留精确调用点身份。"""
        if node.type == "object_creation_expression":
            qualified_name = self._text(_field(node, "type"))
            receiver = ""
        else:
            name = self._text(_field(node, "name"))
            receiver_node = _field(node, "object")
            receiver = self._text(receiver_node)
            qualified_name = self._qualified_method(receiver_node, receiver, name, variable_types)
        arguments_node = _field(node, "arguments")
        arguments = tuple(
            self._expression(item)
            for item in (arguments_node.named_children if arguments_node is not None else [])
        )
        location = self._location(node)
        callsite_id = ProgramIdentity.callsite_id(
            self.path,
            location.line,
            location.column,
            location.end_line,
            location.end_column,
        )
        return IRCall(
            qualified_name=qualified_name,
            callsite_id=callsite_id,
            symbol=symbol,
            location=location,
            arguments=arguments,
            assigned_targets=self._assigned_targets.get(callsite_id, ()),
            receiver=receiver,
        )

    def _qualified_method(
        self,
        receiver_node: Any | None,
        receiver: str,
        name: str,
        variable_types: dict[str, str],
    ) -> str:
        """优先用可观察声明类型限定接收者，失败时保留源码接收者。"""
        if not receiver:
            return name
        if receiver in variable_types:
            return f"{variable_types[receiver]}.{name}"
        if receiver_node is not None and receiver_node.type == "object_creation_expression":
            created_type = self._text(_field(receiver_node, "type"))
            if created_type:
                return f"{created_type}.{name}"
        return f"{receiver}.{name}"

    def _parameter(self, node: Any) -> IRParameter:
        """提取参数名、声明类型和参数级注解文本。"""
        name_node = _field(node, "name")
        type_text = self._text(_field(node, "type"))
        annotations = [
            self._text(item)
            for item in self._descendants(node, _ANNOTATION_NODES)
        ]
        annotation = " ".join((*annotations, type_text)).strip()
        return IRParameter(
            name=self._text(name_node),
            annotation=annotation,
            default_call="",
            location=self._location(node),
        )

    def _decorators(self, node: Any) -> tuple[IRDecorator, ...]:
        """将 Java 方法注解规范化为跨语言入口规则可消费的装饰器。"""
        result: list[IRDecorator] = []
        modifiers = next(
            (child for child in node.named_children if child.type == "modifiers"),
            None,
        )
        if modifiers is None:
            return ()
        for annotation in self._descendants(
            modifiers,
            _ANNOTATION_NODES,
            include_root=True,
        ):
            name = self._text(_field(annotation, "name"))
            arguments_node = _field(annotation, "arguments")
            arguments = tuple(
                self._expression(item)
                for item in (
                    arguments_node.named_children
                    if arguments_node is not None else []
                )
                if item.type != "element_value_pair"
            )
            result.append(IRDecorator(qualified_name=name, arguments=arguments))
        return tuple(result)

    def _owner_types(self, node: Any) -> tuple[str, ...]:
        """记录 extends/implements 中直接观察到的类型，供 Servlet 入口识别。"""
        result: list[str] = []
        for child in node.named_children:
            if child.type in {
                "superclass", "super_interfaces", "extends_interfaces",
                "type_list",
            }:
                result.extend(self._type_names(child))
        return tuple(dict.fromkeys(result))

    def _type_names(self, node: Any) -> list[str]:
        """从继承声明中提取类型文本，同时忽略语法包装节点。"""
        names = [
            self._text(item)
            for item in self._descendants(
                node,
                {"type_identifier", "scoped_type_identifier", "generic_type"},
            )
        ]
        return [name for name in names if name]

    def _local_variable_types(self, body: Any) -> dict[str, str]:
        """建立方法内简单变量到声明类型的词法映射。"""
        result: dict[str, str] = {}
        for declaration in self._descendants(body, {"local_variable_declaration"}):
            declared_type = self._text(_field(declaration, "type"))
            for child in declaration.named_children:
                if child.type != "variable_declarator":
                    continue
                name = self._text(_field(child, "name"))
                if name and declared_type:
                    result[name] = declared_type
        return result

    def _collect_assigned_targets(self, root: Any) -> dict[str, tuple[str, ...]]:
        """按共享 callsite_id 索引变量声明和普通赋值右侧的所有调用。"""
        result: dict[str, list[str]] = {}
        for node in self._walk(root):
            if node.type == "variable_declarator":
                target = self._text(_field(node, "name"))
                value = _field(node, "value")
            elif node.type == "assignment_expression":
                target = self._text(_field(node, "left"))
                value = _field(node, "right")
            else:
                continue
            if not target or value is None:
                continue
            for call in self._descendants(value, _CALL_NODES, include_root=True):
                location = self._location(call)
                callsite_id = ProgramIdentity.callsite_id(
                    self.path,
                    location.line,
                    location.column,
                    location.end_line,
                    location.end_column,
                )
                result.setdefault(callsite_id, []).append(target)
        return {
            key: tuple(dict.fromkeys(values))
            for key, values in result.items()
        }

    def _expression(self, node: Any) -> IRExpression:
        """保留表达式文本、变量标识符、字面量和值调用形态。"""
        literal = self._literal(node)
        return IRExpression(
            text=self._text(node)[:1000],
            identifiers=self._identifiers(node),
            is_literal=node.type in _LITERAL_NODES,
            literal=literal,
            kind=node.type,
            contains_call=any(item.type in _CALL_NODES for item in self._walk(node)),
        )

    def _literal(self, node: Any) -> Any:
        """在不执行代码的前提下解析常见 Java 标量字面量。"""
        raw = self._text(node)
        if node.type in {"string_literal", "character_literal"}:
            try:
                return ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                return raw[1:-1] if len(raw) >= 2 else raw
        if node.type == "true":
            return True
        if node.type == "false":
            return False
        if node.type == "null_literal":
            return None
        if "integer_literal" in node.type:
            try:
                return int(raw.rstrip("lL").replace("_", ""), 0)
            except ValueError:
                return raw
        if "floating_point_literal" in node.type:
            try:
                return float(raw.rstrip("fFdD").replace("_", ""))
            except ValueError:
                return raw
        return None

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """提取表达式中的运行时标识符并按首次出现稳定去重。"""
        if node is None:
            return ()
        return tuple(dict.fromkeys(
            self._text(item)
            for item in self._walk(node)
            if item.type == "identifier" and self._text(item)
        ))

    def _location(self, node: Any) -> IRLocation:
        """将 Tree-sitter 零基坐标转换为统一一基位置。"""
        line = int(node.start_point[0]) + 1
        column = int(node.start_point[1]) + 1
        end_line = int(node.end_point[0]) + 1
        end_column = int(node.end_point[1]) + 1
        return IRLocation(
            path=self.path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            location_id=ProgramIdentity.location_id(
                self.path, line, column, end_line, end_column,
            ),
        )

    @staticmethod
    def _parameter_nodes(node: Any) -> list[Any]:
        """返回普通参数、可变参数和 receiver 参数。"""
        parameters = _field(node, "parameters")
        if parameters is None:
            return []
        return [
            child for child in parameters.named_children
            if child.type in {"formal_parameter", "spread_parameter", "receiver_parameter"}
        ]

    @staticmethod
    def _walk(node: Any) -> list[Any]:
        """以源码顺序返回包含根节点的具名子树节点。"""
        result: list[Any] = []
        pending = [node]
        while pending:
            current = pending.pop()
            result.append(current)
            pending.extend(reversed(current.named_children))
        return result

    @classmethod
    def _descendants(
        cls,
        node: Any,
        node_types: set[str],
        *,
        include_root: bool = False,
    ) -> list[Any]:
        """返回指定类型的后代节点。"""
        nodes = cls._walk(node)
        if not include_root:
            nodes = nodes[1:]
        return [item for item in nodes if item.type in node_types]

    def _text(self, node: Any | None) -> str:
        """容错读取节点对应 UTF-8 文本。"""
        return _text(node, self.source)
