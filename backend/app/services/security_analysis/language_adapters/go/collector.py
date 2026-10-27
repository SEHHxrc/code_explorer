"""将 Go Tree-sitter AST 转换为规则无关的安全分析 IR。"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import semantic_children
from backend.app.services.syntax_analysis import field as _field
from backend.app.services.syntax_analysis import fields as _fields
from backend.app.services.syntax_analysis import parse_static_index_literal
from backend.app.services.syntax_analysis.go import GO_FUNCTION_NODES, go_callable_parts

from ...ir import (
    IRCall,
    IRAccess,
    IRCondition,
    IRExpression,
    IRFunction,
    IRParameter,
)
from ..treesitter import TreeSitterIRCollector
from .api import GoAPIBindings, PACKAGES, REQUEST_FIELDS, ROUTERS

_FUNCTION_NODES = GO_FUNCTION_NODES
_CALL_NODES = {"call_expression"}
_ASSIGNMENT_NODES = {"short_var_declaration", "assignment_statement"}
_LITERAL_NODES = {
    "interpreted_string_literal", "raw_string_literal", "rune_literal",
    "int_literal", "float_literal", "imaginary_literal", "true", "false", "nil",
}


class _GoIRCollector(TreeSitterIRCollector):
    """维护 Go 函数/接收者作用域并生成稳定安全 IR 身份。"""

    def __init__(self, path: str, source: bytes, root: Any) -> None:
        """保存源码并预索引导入别名和调用返回值目标。"""
        self.path = ProgramIdentity.file_id(path)
        self.source = source
        self.functions: list[IRFunction] = []
        self.calls: list[IRCall] = []
        self.conditions: list[IRCondition] = []
        self.accesses: list[IRAccess] = []
        self._imports = self._import_aliases(root)
        self._api = GoAPIBindings(source, self._imports)
        self._assigned_targets = self._collect_assigned_targets(root)

    def collect(self, root: Any) -> None:
        """收集顶层函数和接收者方法。"""
        for node in self._walk(root):
            if node.type in _FUNCTION_NODES:
                self._append_function(node)

    def _append_function(self, node: Any) -> None:
        """构造函数 IR，并扫描函数体的调用和条件。"""
        parts = go_callable_parts(node, self.source)
        if not parts:
            return
        name = parts[-1]
        receiver_name, receiver_type = self._receiver(node)
        symbol = ProgramIdentity.symbol_id(self.path, parts)
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
            parameter.name: self._api.canonical_type(parameter.annotation)
            for parameter in parameters
            if parameter.annotation
        }
        if receiver_name and receiver_type:
            variable_types[receiver_name] = receiver_type
        body = _field(node, "body")
        if body is None:
            return
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
        if node.type in {"selector_expression", "index_expression"}:
            self._access(node, symbol, variable_types)
        if node.type == "if_statement":
            condition = _field(node, "condition")
            if condition is not None:
                self.conditions.append(IRCondition(
                    kind="if",
                    symbol=symbol,
                    expression=self._expression(condition, variable_types),
                    location=self._location(condition),
                ))
        scoped = node.type in {"if_statement", "for_statement", "expression_switch_statement", "select_statement", "block"}
        environment = dict(variable_types) if scoped else variable_types
        for child in node.named_children:
            self._walk_body(child, symbol, environment)
        if node.type in _ASSIGNMENT_NODES or node.type == "var_spec":
            self._bind_types(node, variable_types)
        if scoped:
            # 内层声明不泄漏；已存在变量若被重新绑定则在外层失效，避免跨分支冒充 Request。
            for name in variable_types.keys() & environment.keys():
                if environment[name] != variable_types[name]:
                    variable_types[name] = ""

    def _bind_types(self, node: Any, variables: dict[str, str]) -> None:
        """按执行词法顺序更新已观察类型；未知重绑定清空角色，错误返回不继承内容类型。"""
        targets = self._expression_items(_field(node, "left")) if node.type in _ASSIGNMENT_NODES else list(_fields(node, "name"))
        values = self._expression_items(_field(node, "right" if node.type in _ASSIGNMENT_NODES else "value"))
        declared = self._api.canonical_type(self._text(_field(node, "type")))
        roles = (self._api.result_types(values[0], variables) if len(values) == 1 and values[0].type == "call_expression"
                 else tuple(self._api.expression_type(value, variables) for value in values))
        prefixes = tuple(self._api.route_prefix(value, variables) if index < len(roles) and roles[index] in ROUTERS else "<dynamic>"
                         for index, value in enumerate(values))
        handlers = tuple(self._callable_symbol(value, variables) for value in values)
        for index, target in enumerate(targets):
            if target.type == "identifier":
                name = self._text(target)
                variables[name] = declared or (roles[index] if index < len(roles) else "")
                variables["@gin-prefix:" + name] = prefixes[index] if index < len(prefixes) else "<dynamic>"
                variables["@go-handler:" + name] = handlers[index] if index < len(handlers) else ""
            elif target.type in {"selector_expression", "index_expression"}:
                variables["@go-written:" + self._text(target)] = "written"

    def _access(self, node: Any, symbol: str, variables: dict[str, str]) -> None:
        """记录请求字段/静态索引的原始值槽位，不把本地 Header、输出 Writer 或整个 Context 污染。"""
        owner = _field(node, "operand")
        current = node
        while current.parent is not None and current.parent.type not in _FUNCTION_NODES | {"block"}:
            parent = current.parent
            if parent.type in _ASSIGNMENT_NODES:
                left = _field(parent, "left")
                if left is not None and left.start_byte <= node.start_byte and node.end_byte <= left.end_byte:
                    return
                break
            current = parent
        if variables.get("@go-written:" + self._text(node)) == "written":
            return
        role = self._api.expression_type(owner, variables)
        member = self._text(_field(node, "field"))
        qualified = ""
        if node.type == "selector_expression" and (
            role == "http.Request" and member in REQUEST_FIELDS
            or role == "http.Request.URL" and "URL." + member in REQUEST_FIELDS
            or role == "multipart.FileHeader" and member == "Filename"
            or role == "http.Request.Cookie" and member == "Value"
        ):
            qualified = role + "." + member
        elif node.type == "index_expression" and role in {"http.Request.Header", "http.Request.Form", "http.Request.PostForm", "http.Request.QueryValues"}:
            qualified = role + ".item"
        if qualified:
            self.accesses.append(IRAccess(qualified_name=qualified, symbol=symbol, access_kind="read",
                location=self._location(node), value_identifiers=(self._text(node),), assigned_targets=self._access_targets(node),
                resolution_method="go_import_and_lexical_type"))

    def _access_targets(self, node: Any) -> tuple[str, ...]:
        """只返回包含本次读取的 RHS 对应目标；不跨函数、代码块或其他并行分量串值。"""
        parent = node.parent
        while parent is not None and parent.type not in _FUNCTION_NODES | {"block"}:
            if parent.type in _ASSIGNMENT_NODES | {"var_spec", "const_spec"}:
                right = _field(parent, "right" if parent.type in _ASSIGNMENT_NODES else "value")
                if right is None or not (right.start_byte <= node.start_byte and node.end_byte <= right.end_byte):
                    return ()
                targets = self._expression_items(_field(parent, "left")) if parent.type in _ASSIGNMENT_NODES else list(_fields(parent, "name"))
                values = self._expression_items(right)
                if len(values) == len(targets):
                    return tuple(self._text(target) for target, value in zip(targets, values)
                                 if value.start_byte <= node.start_byte and node.end_byte <= value.end_byte)
                return tuple(self._text(target) for target in targets)
            parent = parent.parent
        return ()

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
        qualified_name = self._api.call_name(node, variable_types)
        arguments_node = _field(node, "arguments")
        arguments = tuple(
            self._expression(item, variable_types)
            for item in semantic_children(arguments_node)
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
            receiver_api_type=self._api.expression_type(receiver_node, variable_types),
            route_prefix=self._api.route_prefix(receiver_node, variable_types)
                if self._api.expression_type(receiver_node, variable_types) in ROUTERS else "",
        )

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
        receiver_type = self._api.canonical_type(self._text(_field(parameter, "type")))
        return name, receiver_type

    def _parameters(self, node: Any | None) -> list[IRParameter]:
        """展开一个声明中包含多个名称的 Go 参数。"""
        result: list[IRParameter] = []
        for parameter in (node.named_children if node is not None else ()):
            if parameter.type not in {"parameter_declaration", "variadic_parameter_declaration"}:
                continue
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
                        kind="variadic_positional" if parameter.type == "variadic_parameter_declaration" else "positional_or_keyword",
                    ))
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
                result[alias] = PACKAGES.get(import_path, import_path)
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
                for call in ([value] if value.type in _CALL_NODES else []):
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

    def _expression(self, node: Any, variables: dict[str, str] | None = None) -> IRExpression:
        """保留表达式文本、标识符、字面量和调用形态。"""
        variables = variables or {}
        if node.type == "func_literal":
            return IRExpression(text=self._text(node)[:1000], kind="function", callable_symbol=self._callable_symbol(node, variables))
        if node.type == "call_expression" and self._api.call_name(node, variables) == "http.HandlerFunc":
            args = semantic_children(_field(node, "arguments"))
            if len(args) == 1:
                return self._expression(args[0], variables)
        expression = self._sequence_expression(node, self._literal(node), _LITERAL_NODES, _CALL_NODES)
        operand = _field(node, "operand")
        reference = (self._text(operand) if node.type == "unary_expression" and self._text(node).startswith("&")
                     and operand is not None and operand.type == "identifier" else "")
        return replace(expression, api_type=self._api.expression_type(node, variables),
                       callable_owner_type=self._api.expression_type(operand, variables) if node.type == "selector_expression" else "",
                       callable_symbol=variables.get("@go-handler:" + self._text(node), "") if node.type == "identifier" else "",
                       callable_unresolved=node.type == "identifier" and self._text(node) in variables
                           and not variables.get("@go-handler:" + self._text(node)),
                       reference_target=reference)

    def _callable_symbol(self, node: Any, variables: dict[str, str]) -> str:
        """读取已知匿名函数/局部别名身份；不为普通赋值重复遍历整棵表达式子树。"""
        if node.type == "func_literal":
            return ProgramIdentity.symbol_id(self.path, go_callable_parts(node, self.source))
        if node.type == "identifier":
            return variables.get("@go-handler:" + self._text(node), "")
        return ""

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
