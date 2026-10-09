"""C/C++ 安全 IR 前端；共享语法工具和身份，不构建 CFG 或跨文件调用图。"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.syntax_analysis import (
    extensions_for_language,
    field,
    fields,
    parse_static_index_literal,
    split_qualified,
    text,
    unwrap_c_declarator,
)

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


class CSecurityFrontend(TreeSitterSecurityFrontend):
    """将 C 函数、参数、调用和条件转换为公共安全 IR。"""

    language = "c"
    parser_name = "c"
    extensions = extensions_for_language(language)

    def build(
        self,
        project_root: str | Path,
        *,
        file_paths: list[str] | None = None,
    ) -> SecurityProgramIR:
        """复用公共文件编排，并排除本次源码范围中同名项目函数的库 API 匹配。"""
        program = super().build(project_root, file_paths=file_paths)
        project_names = {function.name for function in program.functions}
        program.calls = [
            replace(call, callee_is_project_defined=True)
            if call.qualified_name.removeprefix("std.") in project_names
            else call
            for call in program.calls
        ]
        return program

    def collect_file(
        self,
        program: SecurityProgramIR,
        path: str,
        source: bytes,
        root: Any,
    ) -> None:
        """输入文件 AST，将声明、调用、条件及未建模语义追加到项目 IR。"""
        collector = _CIRCollector(path, source, program)
        collector.collect(root)


class CppSecurityFrontend(CSecurityFrontend):
    """复用 C 声明符，补充 C++ 类、命名空间与限定调用语法。"""

    language = "cpp"
    parser_name = "cpp"
    extensions = extensions_for_language(language)


class _CIRCollector:
    """收集规则无关语法事实；库名匹配和安全参数角色仍由规则包拥有。"""

    def __init__(self, path: str, source: bytes, program: SecurityProgramIR) -> None:
        """保存源码、输出 IR 和文件级词法名称遮蔽索引。"""
        self.path = ProgramIdentity.file_id(path)
        self.source = source
        self.program = program
        self.headers: tuple[str, ...] = ()
        self.shadows: set[str] = set()
        self.assigned_targets: dict[str, tuple[str, ...]] = {}

    def collect(self, root: Any) -> None:
        """索引头文件、声明与精确返回值目标，再按作用域收集函数。"""
        headers: set[str] = set()
        for node in self._walk(root):
            if node.type == "preproc_include":
                headers.add(self._text(field(node, "path")).strip('<>"'))
            elif node.type in {"preproc_def", "preproc_function_def"}:
                self.shadows.add(self._text(field(node, "name")))
                self._diagnostic(node, "macro_expansion_not_modeled")
            elif node.type in {"declaration", "field_declaration", "function_definition"}:
                for declarator in fields(node, "declarator"):
                    target = field(declarator, "declarator") or declarator
                    name, _parameters = unwrap_c_declarator(target)
                    self.shadows.add(self._text(name))
            if node.type in {"init_declarator", "assignment_expression"}:
                target = field(node, "declarator") or field(node, "left")
                value = field(node, "value") or field(node, "right")
                if node.type == "init_declarator":
                    target, _parameters = unwrap_c_declarator(target)
                # 只把直接调用的结果绑定到该 declarator；兄弟变量和嵌套调用不共享目标。
                while value is not None and value.type in {"parenthesized_expression", "cast_expression"}:
                    value = field(value, "value") or next(iter(value.named_children), None)
                if target is not None and value is not None and value.type == "call_expression":
                    self.assigned_targets[self._callsite(value)] = (self._text(target),)
        self.headers = tuple(sorted(headers))
        self._collect_scopes(root, ())

    def _collect_scopes(self, node: Any, scope: tuple[str, ...]) -> None:
        """保留 namespace/class/struct 作用域，并在函数体入口切换为调用扫描。"""
        if node.type == "function_definition":
            self._function(node, scope)
            return
        if node.type in {"namespace_definition", "class_specifier", "struct_specifier"}:
            scope = scope + (self._text(field(node, "name")) or "anonymous",)
        for child in node.named_children:
            self._collect_scopes(child, scope)

    def _function(self, node: Any, scope: tuple[str, ...]) -> None:
        """生成与 ProgramGraph 相同的词法函数身份，并收集函数体语法事实。"""
        name_node, parameter_list = unwrap_c_declarator(field(node, "declarator"))
        parts = split_qualified(self._text(name_node))
        if not parts:
            return
        symbol = ProgramIdentity.symbol_id(self.path, scope + tuple(parts))
        parameters: list[IRParameter] = []
        for parameter in parameter_list.named_children if parameter_list is not None else []:
            if parameter.type not in {"parameter_declaration", "optional_parameter_declaration"}:
                continue
            name, _nested = unwrap_c_declarator(field(parameter, "declarator"))
            if name is not None:
                parameters.append(IRParameter(
                    name=self._text(name),
                    annotation=self._text(field(parameter, "type")),
                    default_call="",
                    location=self._location(name),
                ))
        self.program.functions.append(IRFunction(
            symbol=symbol,
            name=parts[-1],
            parameters=tuple(parameters),
            decorators=(),
            location=self._location(node),
            owner_types=scope,
        ))
        if parts == ["main"] and not scope and parameters:
            self._diagnostic(node, "cli_parameter_dataflow_not_modeled")
        body = field(node, "body")
        if body is not None:
            shadows = self.shadows | {parameter.name for parameter in parameters}
            self._body(body, symbol, shadows)

    def _body(self, node: Any, symbol: str, shadows: set[str]) -> None:
        """按函数作用域扫描调用和条件，显式排除未建模 lambda 与嵌套函数。"""
        if node.type in {"lambda_expression", "function_definition"}:
            self._diagnostic(node, "nested_function_dataflow_not_modeled")
            return
        if node.type == "call_expression":
            function = field(node, "function")
            raw_name = self._text(function).removeprefix("::")
            name = raw_name.replace("::", ".")
            arguments = field(node, "arguments")
            self.program.calls.append(IRCall(
                qualified_name=name,
                callsite_id=self._callsite(node),
                symbol=symbol,
                location=self._location(node),
                arguments=tuple(self._expression(item) for item in arguments.named_children)
                if arguments is not None else (),
                assigned_targets=self.assigned_targets.get(self._callsite(node), ()),
                visible_headers=self.headers,
                callee_is_project_defined=(raw_name in shadows or name.removeprefix("std.") in shadows),
            ))
            if node.parent is not None and node.parent.type == "argument_list":
                self._diagnostic(node, "nested_call_result_binding_not_modeled")
        if node.type == "if_statement":
            condition = field(node, "condition")
            if condition is not None:
                self.program.conditions.append(IRCondition(
                    kind="if", symbol=symbol,
                    expression=self._expression(condition),
                    location=self._location(condition),
                ))
        for child in node.named_children:
            self._body(child, symbol, shadows)

    def _expression(self, node: Any) -> IRExpression:
        """保留实参文本、标识符与可静态求值常量，供公共规则条件使用。"""
        raw = self._text(node)
        literal: Any = None
        is_literal = node.type in {"string_literal", "char_literal", "number_literal", "true", "false", "null"}
        if node.type in {"string_literal", "char_literal"}:
            try:
                literal = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                is_literal = False
        elif node.type == "number_literal":
            literal = parse_static_index_literal(raw)
            is_literal = literal is not None
        elif node.type in {"true", "false"}:
            literal = node.type == "true"
        return IRExpression(
            text=raw[:1000],
            identifiers=tuple(dict.fromkeys(
                self._text(item) for item in self._walk(node)
                if item.type == "identifier"
            )),
            is_literal=is_literal,
            literal=literal,
            kind=node.type,
            contains_call=any(item.type == "call_expression" for item in self._walk(node)),
        )

    def _callsite(self, node: Any) -> str:
        """使用精确 AST 范围计算与依赖图和 ProgramGraph 相同的调用点 ID。"""
        location = self._location(node)
        return ProgramIdentity.callsite_id(
            self.path, location.line, location.column, location.end_line, location.end_column,
        )

    def _location(self, node: Any) -> IRLocation:
        """输入 AST 节点，返回一基坐标和稳定源码位置身份。"""
        line, column = int(node.start_point[0]) + 1, int(node.start_point[1]) + 1
        end_line, end_column = int(node.end_point[0]) + 1, int(node.end_point[1]) + 1
        return IRLocation(
            path=self.path, line=line, column=column, end_line=end_line, end_column=end_column,
            location_id=ProgramIdentity.location_id(self.path, line, column, end_line, end_column),
        )

    def _diagnostic(self, node: Any, reason: str) -> None:
        """为尚未建模的语法记录精确位置，使覆盖局限进入持久化证据。"""
        self.program.failures.append({
            "path": self.path, "line": int(node.start_point[0]) + 1,
            "language": self.program.language, "reason": reason,
        })

    def _text(self, node: Any | None) -> str:
        """读取有界 AST 节点对应源码。"""
        return text(node, self.source)

    @staticmethod
    def _walk(node: Any) -> list[Any]:
        """按源码顺序返回具名节点；只在本次文件收集期间持有 AST。"""
        result: list[Any] = []
        pending = [node]
        while pending:
            current = pending.pop()
            result.append(current)
            pending.extend(reversed(current.named_children))
        return result
