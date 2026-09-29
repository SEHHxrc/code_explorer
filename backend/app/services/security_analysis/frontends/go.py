"""将 Go Tree-sitter AST 转换为规则无关的安全分析 IR。"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.program_graph.frontends.access_paths import (
    parse_static_index_literal,
)
from backend.app.services.syntax_analysis import extensions_for_language
from backend.app.services.syntax_analysis import field as _field
from backend.app.services.syntax_analysis import fields as _fields
from backend.app.services.syntax_analysis import normalize_type as _norm_type
from backend.app.services.syntax_analysis import text as _text

from ..ir import (
    IRCall,
    IRCondition,
    IRExpression,
    IRFunction,
    IRLocation,
    IRParameter,
    SecurityProgramIR,
)
from .treesitter import TreeSitterSecurityFrontend

_FUNCTION_NODES = {"function_declaration", "method_declaration"}
_CALL_NODES = {"call_expression"}
_ASSIGNMENT_NODES = {"short_var_declaration", "assignment_statement"}
_LITERAL_NODES = {
    "interpreted_string_literal", "raw_string_literal", "rune_literal",
    "int_literal", "float_literal", "imaginary_literal", "true", "false", "nil",
}


class GoSecurityFrontend(TreeSitterSecurityFrontend):
    """提取 Go 函数、调用、赋值目标、参数类型和条件。"""

    language = "go"
    parser_name = "go"
    extensions = extensions_for_language(language)

    def collect_file(
        self,
        program: SecurityProgramIR,
        path: str,
        source: bytes,
        root: Any,
    ) -> None:
        """将一个 Go 文件的可观察语法事实追加到公共 IR。"""
        collector = _GoIRCollector(path, source, root)
        collector.collect(root)
        program.functions.extend(collector.functions)
        program.calls.extend(collector.calls)
        program.conditions.extend(collector.conditions)


class _GoIRCollector:
    """维护 Go 函数/接收者作用域并生成稳定安全 IR 身份。"""

    def __init__(self, path: str, source: bytes, root: Any) -> None:
        """保存源码并预索引导入别名和调用返回值目标。"""
        self.path = ProgramIdentity.file_id(path)
        self.source = source
        self.functions: list[IRFunction] = []
        self.calls: list[IRCall] = []
        self.conditions: list[IRCondition] = []
        self._imports = self._import_aliases(root)
        self._assigned_targets = self._collect_assigned_targets(root)

    def collect(self, root: Any) -> None:
        """收集顶层函数和接收者方法。"""
        for node in self._walk(root):
            if node.type in _FUNCTION_NODES:
                self._append_function(node)

    def _append_function(self, node: Any) -> None:
        """构造函数 IR，并扫描函数体的调用和条件。"""
        name = self._text(_field(node, "name"))
        if not name:
            return
        receiver_name, receiver_type = self._receiver(node)
        scope = (receiver_type,) if receiver_type else ()
        symbol = ProgramIdentity.symbol_id(self.path, scope + (name,))
        parameters = tuple(self._parameters(_field(node, "parameters")))
        self.functions.append(IRFunction(
            symbol=symbol,
            name=name,
            parameters=parameters,
            decorators=(),
            location=self._location(node),
            owner_types=(receiver_type,) if receiver_type else (),
        ))
        variable_types = {
            parameter.name: _norm_type(parameter.annotation)
            for parameter in parameters
            if parameter.annotation
        }
        if receiver_name and receiver_type:
            variable_types[receiver_name] = receiver_type
        body = _field(node, "body")
        if body is None:
            return
        variable_types.update(self._local_variable_types(body))
        self._walk_body(body, symbol, variable_types)

    def _walk_body(
        self,
        node: Any,
        symbol: str,
        variable_types: dict[str, str],
    ) -> None:
        """一次遍历收集函数体中的调用点和 if 条件。"""
        if node.type in _FUNCTION_NODES:
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
        for child in node.named_children:
            self._walk_body(child, symbol, variable_types)

    def _call(
        self,
        node: Any,
        symbol: str,
        variable_types: dict[str, str],
    ) -> IRCall:
        """规范化 Go 函数/方法调用并保留共享 callsite_id。"""
        function = _field(node, "function")
        receiver_node = _field(function, "operand") if function is not None else None
        receiver = self._text(receiver_node)
        if function is not None and function.type == "selector_expression":
            name = self._text(_field(function, "field"))
            qualified_name = self._qualified_method(receiver, name, variable_types)
        else:
            qualified_name = self._text(function)
        arguments_node = _field(node, "arguments")
        arguments = tuple(
            self._expression(item)
            for item in (
                arguments_node.named_children
                if arguments_node is not None else []
            )
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
        receiver: str,
        name: str,
        variable_types: dict[str, str],
    ) -> str:
        """用导入包或可观察变量类型限定 selector 调用。"""
        if not receiver:
            return name
        if receiver in self._imports:
            return f"{self._imports[receiver]}.{name}"
        receiver_type = variable_types.get(receiver)
        if receiver_type:
            return f"{receiver_type}.{name}"
        return f"{receiver}.{name}"

    def _receiver(self, node: Any) -> tuple[str, str]:
        """返回 Go 方法接收者名称和规范类型。"""
        receiver = _field(node, "receiver")
        parameter = next(iter(self._descendants(
            receiver,
            {"parameter_declaration"},
        )), None)
        if parameter is None:
            return "", ""
        names = _fields(parameter, "name")
        name = self._text(names[0]) if names else ""
        receiver_type = _norm_type(self._text(_field(parameter, "type")))
        return name, receiver_type

    def _parameters(self, node: Any | None) -> list[IRParameter]:
        """展开一个声明中包含多个名称的 Go 参数。"""
        result: list[IRParameter] = []
        for parameter in self._descendants(
            node,
            {"parameter_declaration", "variadic_parameter_declaration"},
        ):
            annotation = self._text(_field(parameter, "type")) or "?"
            names = _fields(parameter, "name")
            for name_node in names:
                name = self._text(name_node)
                if name:
                    result.append(IRParameter(
                        name=name,
                        annotation=annotation,
                        default_call="",
                        location=self._location(name_node),
                    ))
        return result

    def _local_variable_types(self, body: Any) -> dict[str, str]:
        """索引显式 ``var name Type`` 声明供方法限定名解析。"""
        result: dict[str, str] = {}
        for spec in self._descendants(body, {"var_spec"}):
            annotation = _norm_type(self._text(_field(spec, "type")))
            if not annotation:
                continue
            for name_node in _fields(spec, "name"):
                name = self._text(name_node)
                if name:
                    result[name] = annotation
        return result

    def _import_aliases(self, root: Any) -> dict[str, str]:
        """将显式或默认导入别名映射为稳定包名。"""
        result: dict[str, str] = {}
        for spec in self._descendants(root, {"import_spec"}):
            path_node = _field(spec, "path")
            raw_path = self._text(path_node)
            try:
                import_path = ast.literal_eval(raw_path)
            except (SyntaxError, ValueError):
                import_path = raw_path.strip('"`')
            if not isinstance(import_path, str) or not import_path:
                continue
            package = PurePosixPath(import_path).name
            alias = self._text(_field(spec, "name")) or package
            if alias not in {"_", "."}:
                result[alias] = package
        return result

    def _collect_assigned_targets(self, root: Any) -> dict[str, tuple[str, ...]]:
        """按共享调用点身份索引赋值右侧调用对应的目标。"""
        result: dict[str, list[str]] = {}
        for node in self._walk(root):
            if node.type in _ASSIGNMENT_NODES:
                left = _field(node, "left")
                right = _field(node, "right")
                targets = self._expression_items(left)
                values = self._expression_items(right)
            elif node.type in {"var_spec", "const_spec"}:
                targets = list(_fields(node, "name"))
                values = self._expression_items(_field(node, "value"))
            else:
                continue
            if not targets or not values:
                continue
            pairs = (
                list(zip(targets, values))
                if len(targets) == len(values)
                else [(target, value) for target in targets for value in values]
            )
            for target, value in pairs:
                target_name = self._text(target)
                if not target_name:
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
                    result.setdefault(callsite_id, []).append(target_name)
        return {
            callsite_id: tuple(dict.fromkeys(targets))
            for callsite_id, targets in result.items()
        }

    @staticmethod
    def _expression_items(node: Any | None) -> list[Any]:
        """展开 Go expression_list，其余节点保持单项。"""
        if node is None:
            return []
        return list(node.named_children) if node.type == "expression_list" else [node]

    def _expression(self, node: Any) -> IRExpression:
        """保留表达式文本、标识符、字面量和调用形态。"""
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
        """在不执行代码的情况下解析常见 Go 标量字面量。"""
        raw = self._text(node)
        if node.type == "raw_string_literal":
            return raw[1:-1] if len(raw) >= 2 else raw
        if node.type in {"interpreted_string_literal", "rune_literal"}:
            try:
                return ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                return raw[1:-1] if len(raw) >= 2 else raw
        if node.type == "true":
            return True
        if node.type == "false":
            return False
        if node.type == "nil":
            return None
        if node.type == "int_literal":
            parsed = parse_static_index_literal(raw)
            return parsed if isinstance(parsed, int) else raw
        if node.type in {"float_literal", "imaginary_literal"}:
            try:
                return float(raw.rstrip("i").replace("_", ""))
            except ValueError:
                return raw
        return None

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """提取表达式中的运行时变量标识符并稳定去重。"""
        if node is None:
            return ()
        return tuple(dict.fromkeys(
            self._text(item)
            for item in self._walk(node)
            if item.type == "identifier" and self._text(item)
        ))

    def _location(self, node: Any) -> IRLocation:
        """转换 Tree-sitter 坐标并生成共享位置身份。"""
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
    def _walk(node: Any) -> list[Any]:
        """按源码顺序返回包含根节点的具名语法树。"""
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
        node: Any | None,
        node_types: set[str],
        *,
        include_root: bool = False,
    ) -> list[Any]:
        """返回指定类型的后代节点。"""
        if node is None:
            return []
        nodes = cls._walk(node)
        if not include_root:
            nodes = nodes[1:]
        return [item for item in nodes if item.type in node_types]

    def _text(self, node: Any | None) -> str:
        """容错读取节点对应的 UTF-8 文本。"""
        return _text(node, self.source)
