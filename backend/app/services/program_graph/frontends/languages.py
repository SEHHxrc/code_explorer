"""Java/Python 之外语言的轻量 ProgramGraph 适配器。"""

from __future__ import annotations

from typing import Any

from backend.app.services.syntax_analysis import semantic_children
from backend.app.services.value_binding import BindingValue
from backend.app.services.value_binding.returns import ReturnBinding
from backend.app.services.syntax_analysis.javascript_bindings import javascript_pattern, javascript_value, resolve_javascript_binding
from ..control_ir import ControlValueTransfer
from backend.app.services.syntax_analysis.static_sequences import static_sequence_value
from backend.app.services.syntax_analysis.go import GO_FUNCTION_NODES, go_callable_parts
from backend.app.services.value_binding.access_paths import AccessPath, AccessPathSegment

from backend.app.services.syntax_analysis.javascript import (
    JAVASCRIPT_FUNCTION_NODES, javascript_function, javascript_parameters, javascript_scope_nodes,
)

from backend.app.services.syntax_analysis import (
    extensions_for_language,
    unwrap_c_declarator as _unwrap_c_declarator,
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
from .objects import CppInstanceSyntax, GoInstanceSyntax, JavaScriptInstanceSyntax
from .storage import StorageSyntax


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
    instance_syntax = JavaScriptInstanceSyntax()
    block_node_types = TreeSitterFunctionCollector.block_node_types | frozenset({"program"})
    function_node_types = JAVASCRIPT_FUNCTION_NODES
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
        parts = javascript_function(node, self.source)
        if parts is None:
            return None
        name, target = parts
        body = _field(target, "body")
        parameters = javascript_parameters(target, self.source)
        return FunctionDescriptor(
            name=name,
            scope=scope,
            parameters=tuple(item[0] for item in parameters),
            # JS/TS 允许同名重新绑定；使用位置身份比不完整类型推断更可靠。
            parameter_types=(),
            body=body,
            parameter_patterns=tuple(javascript_pattern(item[2], self.source, self.path) for item in parameters),
        ) if name else None

    def _declaration_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """提取 let/const/var 声明中的绑定和值依赖。"""
        definitions: list[str] = []
        uses: list[str] = []
        for declarator in javascript_scope_nodes(node, self.source):
            if declarator.type != "variable_declarator":
                continue
            name_node = _field(declarator, "name")
            value = _field(declarator, "value")
            result = resolve_javascript_binding(name_node, value, self.source, self.path)
            self._limitations.extend(result.diagnostics)
            definitions.extend(item.output for item in result.projections)
            dynamic = self._identifiers(value)
            uses.extend(dynamic if name_node.type == "identifier" and any("[?" in name for name in dynamic)
                        else (name for item in result.projections for name in item.inputs))
        return self._unique(definitions), self._unique(uses)

    def _paired_bindings(self, target: Any, value: Any) -> tuple[tuple[Any, Any], ...]:
        """保留完整 pattern，由公共投影器负责嵌套、空槽和默认值。"""
        return ((target, value),)

    def _binding_transfers(self, target: Any, value: Any, *, compound: bool) -> tuple[ControlValueTransfer, ...]:
        """输出逐字段值流，保留字面量的空输入，防止兄弟字段污点串流。"""
        if compound:
            return super()._binding_transfers(target, value, compound=True)
        outputs, inputs = self._identifiers(target), self._identifiers(value)
        if any("[?" in name for name in (*outputs, *inputs)) and target.type in {"identifier", "member_expression", "subscript_expression"}:
            calls = self._call_sites(value)
            return tuple(ControlValueTransfer(output_variable=name, input_variables=inputs,
                transfer_kind="call_result" if calls else "assignment", callsite_ids=tuple(call.callsite_id for call in calls),
                reference_source=self._direct_reference(value), certainty="may" if calls else "must") for name in outputs)
        result = resolve_javascript_binding(target, value, self.source, self.path)
        self._limitations.extend(result.diagnostics)
        calls = self._call_sites(value)
        reference = self._direct_reference(value)
        return tuple(ControlValueTransfer(
            output_variable=item.output, input_variables=item.inputs,
            transfer_kind="call_result" if calls else "assignment",
            callsite_ids=tuple(call.callsite_id for call in calls),
            certainty="may" if calls else item.certainty,
            reference_source=reference if item.inputs == (reference,) else "",
        ) for item in result.projections)

    def _assignment_data(self, node: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """让赋值解构和声明解构使用相同字段身份；复合赋值保持原有保守行为。"""
        if node.type == "assignment_expression":
            target, value = _field(node, "left"), _field(node, "right")
            outputs, inputs = self._identifiers(target), self._identifiers(value)
            if target is not None and target.type in {"identifier", "member_expression", "subscript_expression"} and any("[?" in name for name in (*outputs, *inputs)):
                return outputs, inputs
            result = resolve_javascript_binding(_field(node, "left"), _field(node, "right"), self.source, self.path)
            self._limitations.extend(result.diagnostics)
            return self._unique(item.output for item in result.projections), self._unique(name for item in result.projections for name in item.inputs)
        if node.type == "expression_statement" and len(node.named_children) == 1:
            child = node.named_children[0]
            if child.type == "parenthesized_expression" and child.named_children:
                child = child.named_children[0]
            if child.type == "assignment_expression":
                return self._assignment_data(child)
        return super()._assignment_data(node)

    def _call_values(self, arguments: list[Any]) -> tuple[BindingValue, ...]:
        """把实参结构交给公共调用绑定器，避免把对象安全字段映射为整个对象。"""
        return tuple(BindingValue(variables=self._identifiers(item)) if any("[?" in name for name in self._identifiers(item))
                     else javascript_value(item, self.source, self.path) for item in arguments)


    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """JS 中 obj['key'] 与 obj.key 同义；其他语言保留自己的下标语义。"""
        path = super().resolve_access_path(node)
        if path is None:
            return None
        return AccessPath(path.root, tuple(
            AccessPathSegment("attribute", item.value)
            if item.kind == "index" and isinstance(item.value, str) and item.value.isidentifier()
            else item for item in path.segments
        ))

    def _declaration_bindings(self, node: Any) -> tuple[tuple[Any, Any], ...]:
        """按 declarator 保留 JS/TS 每个绑定与初始化值的对应关系。"""
        result: list[tuple[Any, Any]] = []
        for declarator in javascript_scope_nodes(node, self.source):
            if declarator.type != "variable_declarator":
                continue
            target = _field(declarator, "name")
            value = _field(declarator, "value")
            if target is None or value is None:
                continue
            result.extend(self._paired_bindings(target, value))
        return tuple(result)

    def _call_arguments(self, node: Any | None) -> tuple[list[Any], list[tuple[str, Any]]]:
        """spread 后实参位置未知，不伪造位置绑定；前面的普通参数仍可使用。"""
        positional, keywords = super()._call_arguments(node)
        for index, argument in enumerate(positional):
            if argument.type == "spread_element":
                self._limitations.append("JS/TS spread 参数的动态长度尚未建模，停止绑定 spread 及之后的实参。")
                return positional[:index], keywords
        return positional, keywords


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
    instance_syntax = GoInstanceSyntax()
    storage_language = True
    memory_access_node_types = frozenset({"unary_expression"})
    function_node_types = GO_FUNCTION_NODES
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

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """Go 显式 * 为独立解引用段，& 只保留取址操作数；隐式字段解引用交给存储环境。"""
        operator = StorageSyntax.operator(node)
        if operator:
            owner = self.resolve_access_path(StorageSyntax.operand(node))
            return owner.attribute("*") if owner is not None and operator == "*" else owner
        return super().resolve_access_path(node)

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """Go 空白标识符没有存储身份，不能成为跨赋值、循环或调用的值连接点。"""
        return tuple(name for name in super()._identifiers(node) if name != "_")

    def _function_descriptor(
        self,
        node: Any,
        scope: tuple[str, ...],
    ) -> FunctionDescriptor | None:
        """将接收者类型纳入方法 FQN，并保留签名参数类型。"""
        parts = go_callable_parts(node, self.source)
        name = parts[-1]
        parameters = self._go_parameters(_field(node, "parameters"))
        method_scope = parts[:-1]
        return FunctionDescriptor(
            name=name,
            scope=method_scope,
            parameters=tuple(item[0] for item in parameters),
            parameter_types=tuple(item[1] or "?" for item in parameters),
            body=_field(node, "body"),
            parameter_kinds={self._text(name): "variadic_positional" for item in _descendants(_field(node, "parameters"), {"variadic_parameter_declaration"}) for name in _fields(item, "name")},
        ) if name else None

    def _go_parameters(self, node: Any | None) -> list[tuple[str, str]]:
        """展开 Go 一个声明中可能包含的多个参数名。"""
        result: list[tuple[str, str]] = []
        for item in (node.named_children if node is not None else ()):
            if item.type not in {"parameter_declaration", "variadic_parameter_declaration"}:
                continue
            type_literal = self._text(_field(item, "type")) or "?"
            names = [self._text(name) for name in _fields(item, "name")]
            if not names:
                names = list(self._identifiers(_field(item, "name")))
            result.extend((name, type_literal) for name in names if name)
        return result

    def _call_values(self, arguments: list[Any]) -> tuple[BindingValue, ...]:
        """Go 直接切片字面量供公共可变参数投影使用，动态切片不猜测成员。"""
        values = tuple(static_sequence_value(item, self._identifiers) for item in arguments)
        if any(item.type == "variadic_argument" and value.kind != "array" for item, value in zip(arguments, values)):
            self._limitations.append("Go 动态切片展开长度/成员未建模，只绑定此前实参；直接字面量切片可按常量槽位展开。")
        return values

    def _call_result_targets(self, node: Any) -> tuple[str, ...]:
        """仅直接调用的 Go 并行接收保留顺序；嵌套调用不归给外层结果。"""
        parent = node.parent
        if parent is None or parent.type != "expression_list" or len(semantic_children(parent)) != 1:
            return ()
        statement = parent.parent
        if statement is None:
            return ()
        if statement.type in {"short_var_declaration", "assignment_statement"} and _field(statement, "right") == parent:
            left = _field(statement, "left")
            return tuple("" if self._text(item) == "_" else self._text(item) for item in semantic_children(left))
        if statement.type in {"var_spec", "const_spec"} and _field(statement, "value") == parent:
            return tuple("" if self._text(item) == "_" else self._text(item) for item in _fields(statement, "name"))
        return ()

    def _return_values(self, node: Any) -> tuple[tuple[str, ...], ...]:
        """按 Go return 表达式分量保留值使用，不将内容与 error 聚合。"""
        values = next((item for item in node.named_children if item.type == "expression_list"), None)
        if values is None:
            self._limitations.append("Go 裸 return 的命名返回值及隐式多返回转发尚未建模。")
            return ()
        return super()._return_values(node)

    def _return_binding(self, node: Any) -> ReturnBinding:
        """Go 显式多表达式 return 是多个 ABI 分量，不是一个元组对象。"""
        expressions = self._return_expressions(node)
        return ReturnBinding(
            mode="multiple" if len(expressions) > 1 else "single" if expressions else "unknown",
            values=tuple(self._return_value(item) for item in expressions),
        )

    def _return_expressions(self, node: Any) -> list[Any]:
        """Go 多返回的有序分量共用公共表达式位置协议。"""
        values = next((item for item in node.named_children if item.type == "expression_list"), None)
        return semantic_children(values)

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
                self._text(name) for name in _fields(spec, "name") if self._text(name) != "_"
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


class _CCollector(TreeSitterFunctionCollector):
    """C/C++ 共享的声明符、赋值和调用降低。"""

    language = "c"
    storage_language = True
    memory_access_node_types = frozenset({"pointer_expression"})
    function_node_types = frozenset({"function_definition"})
    declaration_node_types = frozenset({"declaration"})
    throw_node_types = frozenset({"throw_statement"})
    identifier_node_types = frozenset({"identifier", "field_identifier"})
    member_access_node_types = frozenset({"field_expression"})
    index_access_node_types = frozenset({"subscript_expression"})

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """C/C++ 的 * 与 ->/. 保留可验证存储路径；指针运算和转换不伪装为直接引用。"""
        operator = StorageSyntax.operator(node)
        if operator:
            owner = self.resolve_access_path(StorageSyntax.operand(node))
            return owner.attribute("*") if owner is not None and operator == "*" else owner
        if node is not None and node.type == "parenthesized_expression" and len(node.named_children) == 1:
            return self.resolve_access_path(node.named_children[0])
        return super().resolve_access_path(node)

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
    instance_syntax = CppInstanceSyntax()
    self_node_types = frozenset({"this"})

    def _append_function(self, node: Any, descriptor: FunctionDescriptor) -> None:
        """仅当前类实际声明的裸字段转为 this 槽；不从整个仓库猜字段或覆盖局部声明。"""
        self._cpp_fields = self.instance_syntax.member_fields(node, self.source)
        super()._append_function(node, descriptor)

    def _identifiers(self, node: Any | None) -> tuple[str, ...]:
        """显式 this 和未被词法局部变量遮蔽的裸字段共用字段身份。"""
        names = super()._identifiers(node)
        storage = getattr(self, "_storage_context", None)
        return tuple("this." + name if name in getattr(self, "_cpp_fields", ()) and storage is not None
                     and node is not None and not storage.type_at(name, node) else name for name in names)

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
