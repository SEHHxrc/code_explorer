"""Java/Python 之外语言的轻量 ProgramGraph 适配器。"""

from __future__ import annotations

from typing import Any

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
    normalize_type as _norm_type,
)

from .treesitter import (
    FunctionDescriptor,
    TreeSitterFunctionCollector,
    TreeSitterProgramGraphFrontend,
)


def _descendants(node: Any | None, node_types: set[str]) -> list[Any]:
    """按源码顺序返回子树中指定种类的节点。"""
    if node is None:
        return []
    result: list[Any] = []
    pending = [node]
    while pending:
        current = pending.pop()
        if current.type in node_types:
            result.append(current)
        pending.extend(reversed(current.named_children))
    return result


class _JavaScriptCollector(TreeSitterFunctionCollector):
    """共享 JavaScript 与 TypeScript 的函数和语句降级。"""

    language = "javascript"
    function_node_types = frozenset({
        "function_declaration", "generator_function_declaration",
        "method_definition", "variable_declarator",
    })
    declaration_node_types = frozenset({
        "lexical_declaration", "variable_declaration",
    })
    call_node_types = frozenset({"call_expression", "new_expression"})
    throw_node_types = frozenset({"throw_statement"})
    try_node_types = frozenset({"try_statement"})
    member_access_node_types = frozenset({"member_expression"})
    index_access_node_types = frozenset({"subscript_expression"})
    self_node_types = frozenset({"this"})
    value_sequence_node_types = frozenset({
        "array", "array_pattern", "assignment_pattern",
    })

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """把类声明映射到依赖图一致的类作用域。"""
        if node.type not in {
            "class_declaration", "abstract_class_declaration", "class",
        }:
            return None
        name = self._text(_field(node, "name"))
        return scope + (name,) if name else scope

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """识别具名函数、类方法以及变量绑定的箭头函数。"""
        target = node
        name_node = _field(node, "name")
        if node.type == "variable_declarator":
            target = _field(node, "value")
            if target is None or target.type not in {"arrow_function", "function_expression"}:
                return None
        name = self._text(name_node)
        body = _field(target, "body")
        parameters = self._javascript_parameters(_field(target, "parameters") or target)
        return FunctionDescriptor(
            name=name,
            scope=scope,
            parameters=tuple(item[0] for item in parameters),
            # JS/TS 允许同名重新绑定；使用位置身份比不完整类型推断更可靠。
            parameter_types=(),
            body=body,
        ) if name else None

    def _javascript_parameters(self, node: Any | None) -> list[tuple[str, str]]:
        """提取 JS 标识符和 TS required/optional 参数。"""
        if node is None:
            return []
        candidates = (
            list(node.named_children)
            if node.type == "formal_parameters"
            else [node]
        )
        result: list[tuple[str, str]] = []
        for item in candidates:
            pattern = _field(item, "pattern") or _field(item, "name")
            if item.type == "identifier":
                pattern = item
            name = self._text(pattern)
            if not name:
                names = self._identifiers(item)
                name = names[0] if names else ""
            if name:
                result.append((name, self._text(_field(item, "type")) or "?"))
        return result

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 let/const/var 声明中的绑定和值依赖。"""
        definitions: list[str] = []
        uses: list[str] = []
        for declarator in _descendants(node, {"variable_declarator"}):
            name_node = _field(declarator, "name")
            value = _field(declarator, "value")
            definitions.extend(self._identifiers(name_node))
            uses.extend(self._identifiers(value))
        return self._unique(definitions), self._unique(uses)

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """按 declarator 保留 JS/TS 每个绑定与初始化值的对应关系。"""
        result: list[tuple[Any, Any]] = []
        for declarator in _descendants(node, {"variable_declarator"}):
            target = _field(declarator, "name")
            value = _field(declarator, "value")
            if target is None or value is None:
                continue
            result.extend(self._paired_bindings(target, value))
        return tuple(result)


class JavaScriptProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """JavaScript/JSX ProgramGraph 前端。"""

    language = "javascript"
    parser_name = "javascript"
    extensions = extensions_for_language(language)
    collector_type = _JavaScriptCollector


class _TypeScriptCollector(_JavaScriptCollector):
    """TypeScript 使用与 JavaScript 相同的运行时控制语义。"""

    language = "typescript"

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """补充 TypeScript 抽象类，接口声明没有可执行函数体。"""
        return super()._scope_for_node(node, scope)


class TypeScriptProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """TypeScript/TSX ProgramGraph 前端。"""

    language = "typescript"
    parser_name = "typescript"
    extensions = extensions_for_language(language)
    collector_type = _TypeScriptCollector


class _GoCollector(TreeSitterFunctionCollector):
    """Go 函数、接收者方法和结构化语句降级。"""

    language = "go"
    function_node_types = frozenset({"function_declaration", "method_declaration"})
    declaration_node_types = frozenset({
        "short_var_declaration", "var_declaration", "const_declaration",
    })
    assignment_node_types = TreeSitterFunctionCollector.assignment_node_types | frozenset({
        "assignment_statement",
    })
    block_node_types = TreeSitterFunctionCollector.block_node_types | frozenset({"block"})
    throw_node_types = frozenset()
    member_access_node_types = frozenset({"selector_expression"})
    index_access_node_types = frozenset({"index_expression"})
    value_sequence_node_types = frozenset({"expression_list"})

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """将接收者类型纳入方法 FQN，并保留签名参数类型。"""
        name = self._text(_field(node, "name"))
        parameters = self._go_parameters(_field(node, "parameters"))
        method_scope = scope
        if node.type == "method_declaration":
            receiver = _field(node, "receiver")
            receiver_parameter = next(iter(_descendants(
                receiver,
                {"parameter_declaration"},
            )), None)
            receiver_type = _norm_type(self._text(_field(receiver_parameter, "type")))
            if receiver_type:
                method_scope = (receiver_type,)
        return FunctionDescriptor(
            name=name,
            scope=method_scope,
            parameters=tuple(item[0] for item in parameters),
            parameter_types=tuple(item[1] or "?" for item in parameters),
            body=_field(node, "body"),
        ) if name else None

    def _go_parameters(self, node: Any | None) -> list[tuple[str, str]]:
        """展开 Go 一个声明中可能包含的多个参数名。"""
        result: list[tuple[str, str]] = []
        for item in _descendants(node, {"parameter_declaration", "variadic_parameter_declaration"}):
            type_literal = self._text(_field(item, "type")) or "?"
            names = [self._text(name) for name in _fields(item, "name")]
            if not names:
                names = list(self._identifiers(_field(item, "name")))
            result.extend((name, type_literal) for name in names if name)
        return result

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """处理 := 以及 var/const spec 的并行绑定。"""
        if node.type == "short_var_declaration":
            return (
                self._identifiers(_field(node, "left")),
                self._identifiers(_field(node, "right")),
            )
        definitions: list[str] = []
        uses: list[str] = []
        for spec in _descendants(node, {"var_spec", "const_spec"}):
            definitions.extend(
                self._text(name) for name in _fields(spec, "name")
            )
            uses.extend(self._identifiers(_field(spec, "value")))
        return self._unique(definitions), self._unique(uses)

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """精确保留 Go :=、var 和 const 的并行绑定位置。"""
        if node.type == "short_var_declaration":
            left = _field(node, "left")
            right = _field(node, "right")
            return self._paired_bindings(left, right) if right is not None else ()
        result: list[tuple[Any, Any]] = []
        for spec in _descendants(node, {"var_spec", "const_spec"}):
            targets = tuple(_fields(spec, "name"))
            value = _field(spec, "value")
            if not targets or value is None:
                continue
            values = self._sequence_items(value)
            if len(targets) == len(values):
                result.extend(zip(targets, values))
            else:
                result.extend((target, value) for target in targets)
        return tuple(result)

    def _loop_definitions(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 Go range clause 左侧绑定和右侧集合。"""
        clause = self._child_of_type(node, {"range_clause"})
        if clause is None:
            return (), ()
        return (
            self._identifiers(_field(clause, "left")),
            self._identifiers(_field(clause, "right")),
        )


class GoProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """Go ProgramGraph 前端。"""

    language = "go"
    parser_name = "go"
    extensions = extensions_for_language(language)
    collector_type = _GoCollector


def _unwrap_c_declarator(node: Any | None) -> tuple[Any | None, Any | None]:
    """返回 C/C++ 声明符中的名称节点和参数容器。"""
    current = node
    parameters = None
    wrappers = {
        "pointer_declarator", "array_declarator", "parenthesized_declarator",
        "reference_declarator", "abstract_pointer_declarator", "attributed_declarator",
    }
    for _ in range(16):
        if current is None:
            break
        if current.type in wrappers:
            current = _field(current, "declarator")
            continue
        if current.type == "function_declarator":
            parameters = _field(current, "parameters")
            current = _field(current, "declarator")
            continue
        break
    return current, parameters


class _CCollector(TreeSitterFunctionCollector):
    """C/C++ 共享的声明符、赋值和调用降低。"""

    language = "c"
    function_node_types = frozenset({"function_definition"})
    declaration_node_types = frozenset({"declaration"})
    throw_node_types = frozenset({"throw_statement"})
    identifier_node_types = frozenset({"identifier", "field_identifier"})
    member_access_node_types = frozenset({"field_expression"})
    index_access_node_types = frozenset({"subscript_expression"})

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """C 没有词法类型作用域；C++ 子类会补充。"""
        return None

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """拆解复杂函数声明符并生成签名感知身份。"""
        name_node, parameter_list = _unwrap_c_declarator(_field(node, "declarator"))
        literal = self._text(name_node)
        parts = [part for part in literal.replace("::", ".").split(".") if part]
        name = parts[-1] if parts else literal
        function_scope = scope + tuple(parts[:-1])
        parameters = self._c_parameters(parameter_list)
        return FunctionDescriptor(
            name=name,
            scope=function_scope,
            parameters=tuple(item[0] for item in parameters),
            parameter_types=tuple(item[1] or "?" for item in parameters),
            body=_field(node, "body"),
        ) if name else None

    def _c_parameters(self, node: Any | None) -> list[tuple[str, str]]:
        """提取 C/C++ 参数声明符名称和类型。"""
        result: list[tuple[str, str]] = []
        for parameter in _descendants(node, {"parameter_declaration", "optional_parameter_declaration"}):
            name_node, _ = _unwrap_c_declarator(_field(parameter, "declarator"))
            name = self._text(name_node)
            if name:
                result.append((name, self._text(_field(parameter, "type")) or "?"))
        return result

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取一个 C/C++ 声明中的多个 init_declarator。"""
        definitions: list[str] = []
        uses: list[str] = []
        declarators = _fields(node, "declarator")
        for declarator in declarators:
            target = _field(declarator, "declarator") or declarator
            name_node, parameters = _unwrap_c_declarator(target)
            if parameters is not None:
                continue
            name = self._text(name_node)
            if name:
                definitions.append(name)
            uses.extend(self._identifiers(_field(declarator, "value")))
        return self._unique(definitions), self._unique(uses)

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """按 init_declarator 隔离 C/C++ 同一声明中的初始化值。"""
        result: list[tuple[Any, Any]] = []
        for declarator in _fields(node, "declarator"):
            value = _field(declarator, "value")
            target = _field(declarator, "declarator")
            if target is not None and value is not None:
                result.append((target, value))
        return tuple(result)


class CProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """C ProgramGraph 前端。"""

    language = "c"
    parser_name = "c"
    extensions = extensions_for_language(language)
    collector_type = _CCollector


class _CppCollector(_CCollector):
    """在 C 声明符基础上补充 C++ 类和命名空间作用域。"""

    language = "cpp"

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """将 namespace/class/struct 纳入方法符号身份。"""
        if node.type not in {
            "namespace_definition", "class_specifier", "struct_specifier",
        }:
            return None
        name = self._text(_field(node, "name")) or "anonymous"
        return scope + (name,)


class CppProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """C++ ProgramGraph 前端。"""

    language = "cpp"
    parser_name = "cpp"
    extensions = extensions_for_language(language)
    collector_type = _CppCollector


class _RustCollector(TreeSitterFunctionCollector):
    """Rust 函数、impl 方法和表达式控制流降级。"""

    language = "rust"
    function_node_types = frozenset({"function_item", "function_signature_item"})
    declaration_node_types = frozenset({"let_declaration"})
    call_node_types = frozenset({"call_expression", "macro_invocation"})
    assignment_node_types = TreeSitterFunctionCollector.assignment_node_types | frozenset({
        "assignment_expression", "compound_assignment_expr",
    })
    return_node_types = frozenset({"return_expression"})
    break_node_types = frozenset({"break_expression"})
    continue_node_types = frozenset({"continue_expression"})
    if_node_types = frozenset({"if_expression"})
    loop_node_types = frozenset({
        "while_expression", "for_expression", "loop_expression",
    })
    switch_node_types = frozenset({"match_expression"})
    member_access_node_types = frozenset({"field_expression"})
    index_access_node_types = frozenset({"index_expression"})
    self_node_types = frozenset({"self"})
    value_sequence_node_types = frozenset({"tuple_expression", "tuple_pattern"})

    def _scope_for_node(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        """把模块、trait 和 impl 目标类型映射到依赖图作用域。"""
        if node.type == "impl_item":
            owner = _norm_type(self._text(_field(node, "type")))
            return scope + (owner,) if owner else scope
        if node.type in {"mod_item", "trait_item"}:
            name = self._text(_field(node, "name"))
            return scope + (name,) if name else scope
        return None

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """提取 Rust 参数 pattern/type 和函数体。"""
        name = self._text(_field(node, "name"))
        parameters = self._rust_parameters(_field(node, "parameters"))
        return FunctionDescriptor(
            name=name,
            scope=scope,
            parameters=tuple(item[0] for item in parameters),
            parameter_types=tuple(item[1] or "?" for item in parameters),
            body=_field(node, "body"),
        ) if name else None

    def _rust_parameters(self, node: Any | None) -> list[tuple[str, str]]:
        """提取普通参数和 self 参数。"""
        result: list[tuple[str, str]] = []
        if node is None:
            return result
        for item in node.named_children:
            if item.type == "self_parameter":
                result.append(("self", "Self"))
                continue
            if item.type != "parameter":
                continue
            pattern = _field(item, "pattern")
            name = self._text(pattern)
            if name:
                result.append((name, self._text(_field(item, "type")) or "?"))
        return result

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 let pattern 与初始化表达式。"""
        return (
            self._identifiers(_field(node, "pattern")),
            self._identifiers(_field(node, "value")),
        )

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """按位置绑定 Rust let pattern 与初始化表达式。"""
        pattern = _field(node, "pattern")
        value = _field(node, "value")
        return self._paired_bindings(pattern, value) if value is not None else ()

    def _call_parts(self, node: Any) -> tuple[Any | None, Any | None]:
        """补充 Rust 宏调用的 token_tree 参数容器。"""
        if node.type == "macro_invocation":
            arguments = next(
                (child for child in node.named_children if child.type == "token_tree"),
                None,
            )
            return _field(node, "macro"), arguments
        return super()._call_parts(node)


class RustProgramGraphFrontend(TreeSitterProgramGraphFrontend):
    """Rust ProgramGraph 前端。"""

    language = "rust"
    parser_name = "rust"
    extensions = extensions_for_language(language)
    collector_type = _RustCollector
