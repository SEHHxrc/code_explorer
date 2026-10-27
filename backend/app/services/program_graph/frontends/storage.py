"""C/C++ 与 Go 的存储语法事实：只描述声明/取址/复制，不求解别名或污点。"""

from dataclasses import dataclass, replace
from typing import Any

from backend.app.services.syntax_analysis import field, fields, semantic_children, text, unwrap_c_declarator
from backend.app.services.value_binding.storage import StorageAllocation
from ..control_ir import ControlStatement, ControlValueTransfer


@dataclass(frozen=True)
class _Declaration:
    """一处局部声明及其词法可见范围；类型不作为运行时对象身份。"""

    name: str
    type_literal: str
    position: int
    scope_start: int
    scope_end: int
    value: Any | None = None


class StorageSyntax:
    """跨语言模板；差异限定为 AST 声明、字面量和一元运算，不另外实现传播算法。"""

    def __init__(self, collector: Any, function: Any) -> None:
        """复用当前解析树，建立局部声明索引；不遍历其他文件，不执行代码。"""
        self.collector, self.source, self.language = collector, collector.source, collector.language
        self.declarations: list[_Declaration] = []
        self.addressed: set[str] = set()
        self.parameter_positions: set[int] = set()
        pending = [function]
        while pending:
            node = pending.pop()
            if node is not function and node.type in collector.function_node_types:
                continue
            if node.type in {"parameter_declaration", "optional_parameter_declaration", "variadic_parameter_declaration"}:
                self.parameter_positions.add(node.start_byte)
            if self.operator(node) == "&":
                names = collector._identifiers(self.operand(node))
                if len(names) == 1:
                    self.addressed.add(names[0])
            for name, literal, value in self.records(node):
                scope = node.parent
                while scope is not None and scope.type not in {"block", "compound_statement", "function_definition", "function_declaration", "method_declaration"}:
                    scope = scope.parent
                self.declarations.append(_Declaration(name, literal or self.infer(value, node), node.start_byte,
                    scope.start_byte if scope is not None else function.start_byte,
                    scope.end_byte if scope is not None else function.end_byte, value))
            pending.extend(reversed(node.named_children))

    @staticmethod
    def operand(node: Any | None) -> Any | None:
        """返回一元操作数；其他表达式没有地址/解引用语义。"""
        return (field(node, "operand") or field(node, "argument") or next(iter(node.named_children), None)) if node is not None else None

    @staticmethod
    def operator(node: Any | None) -> str:
        """仅读取语法节点的一元操作符；乘法、字符串与注释不会成为指针操作。"""
        if node is None or node.type not in {"pointer_expression", "unary_expression"}:
            return ""
        return next((child.type for child in node.children if child.type in {"&", "*"}), "")

    def literal(self, node: Any | None) -> Any | None:
        """返回可直接观察的结构体字面量，允许最外层 &，不展开函数返回或动态表达式。"""
        if self.operator(node) == "&":
            node = self.operand(node)
        if node is not None and node.type in {"composite_literal", "compound_literal_expression"}:
            return node
        return None

    def infer(self, value: Any | None, at: Any) -> str:
        """只从直接字面量、既有局部声明和取址推导声明类型；未知类型保留为空。"""
        if value is None:
            return ""
        if self.language == "go" and value.type in {"interpreted_string_literal", "raw_string_literal", "int_literal", "float_literal", "true", "false", "rune_literal"}:
            return {"interpreted_string_literal": "string", "raw_string_literal": "string", "int_literal": "int",
                    "float_literal": "float64", "true": "bool", "false": "bool", "rune_literal": "rune"}[value.type]
        literal = self.literal(value)
        if literal is not None:
            return ("*" if self.operator(value) == "&" else "") + text(field(literal, "type"), self.source)
        names = self.collector._identifiers(self.operand(value) if self.operator(value) else value)
        if len(names) == 1 and names[0].isidentifier():
            raw = self.type_at(names[0], at, before=True)
            return "*" + raw if raw and self.operator(value) == "&" else raw
        return ""

    def type_at(self, name: str, node: Any, *, before: bool = False) -> str:
        """按声明范围、位置和遮蔽查询类型，不用全函数最后一次同名赋值覆盖作用域。"""
        candidates = [item for item in self.declarations if item.name == name and item.scope_start <= node.start_byte < item.scope_end
                      and (item.position < node.start_byte if before else item.position <= node.start_byte)]
        return max(candidates, key=lambda item: (item.scope_start, item.position)).type_literal if candidates else ""

    def records(self, node: Any) -> list[tuple[str, str, Any | None]]:
        """语言 AST 降级为名称、原始类型、初始化表达式；保留 * 与 & 修饰。"""
        result = []
        if self.language == "go":
            if node.type in {"parameter_declaration", "variadic_parameter_declaration", "var_spec"}:
                value = field(node, "value")
                values = semantic_children(value) if value is not None and value.type == "expression_list" else [value]
                for index, name in enumerate(fields(node, "name")):
                    result.append((text(name, self.source), text(field(node, "type"), self.source), values[index] if index < len(values) else None))
            elif node.type == "short_var_declaration":
                left, right = semantic_children(field(node, "left")), semantic_children(field(node, "right"))
                for index, name in enumerate(left):
                    if name.type == "identifier" and text(name, self.source) != "_":
                        result.append((text(name, self.source), "", right[index] if index < len(right) else None))
        elif node.type in {"declaration", "parameter_declaration", "optional_parameter_declaration"}:
            for declarator in fields(node, "declarator"):
                target = field(declarator, "declarator") if declarator.type == "init_declarator" else declarator
                name, parameters = unwrap_c_declarator(target)
                if name is None or parameters is not None:
                    continue
                prefix = self.source[target.start_byte:name.start_byte].decode("utf8", errors="replace")
                raw = text(field(node, "type"), self.source) + "*" * prefix.count("*") + "&" * prefix.count("&")
                if any(child.type == "array_declarator" for child in self.walk(target)):
                    raw += "[]"
                result.append((text(name, self.source), raw, field(declarator, "value")))
        return result

    @staticmethod
    def walk(node: Any) -> list[Any]:
        """有界于当前声明符的小遍历，不读取整个项目。"""
        result, pending = [], [node]
        while pending:
            item = pending.pop()
            result.append(item)
            pending.extend(item.named_children)
        return result

    def parameters(self) -> dict[str, str]:
        """形参只描述传值/引用角色；实际对象与调用上下文仍由公共绑定器确认。"""
        return {item.name: "reference" if "*" in item.type_literal or "&" in item.type_literal
                or self.language == "go" and item.type_literal.strip().startswith(("map[", "[]")) else "value"
                for item in self.declarations if item.position in self.parameter_positions}

    def decorate(self, statement: ControlStatement, node: Any) -> ControlStatement:
        """给原控制语句添加存储事件；Def/Use 和值传递仍由既有 CFG/DFG 消费。"""
        allocations = []
        extras = []
        records = self.records(node)
        if node.type in {"var_declaration", "expression_statement"}:
            records = [record for child in node.named_children for record in self.records(child)]
        values = {name: value for name, _, value in records}
        for name, raw, value in records:
            raw = raw or self.infer(value, node) or self.type_at(name, node)
            if raw and (not ("*" in raw or "&" in raw) or self.literal(value) is not None or name in self.addressed):
                kind = "heap" if self.literal(value) is not None and self.operator(value) == "&" else "scalar_cell" if name in self.addressed and ("*" in raw or "&" in raw) else "value"
                allocations.append((name, StorageAllocation(type_literal=raw, kind=kind)))
            literal = self.literal(value)
            if literal is not None:
                body = field(literal, "body") or field(literal, "value")
                for element in semantic_children(body):
                    key = field(element, "key") or field(element, "designator")
                    part = field(element, "value")
                    field_name = text(key, self.source).lstrip(".")
                    if field_name.isidentifier() and part is not None:
                        names = self.collector._identifiers(part)
                        extras.append(ControlValueTransfer(output_variable=name + "." + field_name, input_variables=names,
                            transfer_kind="assignment", reference_source=names[0] if len(names) == 1 else "",
                            storage_mode="address" if self.operator(part) == "&" else "reference" if len(names) == 1 and "*" in self.type_at(names[0], node) else ""))
        transfers = []
        for transfer in statement.value_transfers:
            value = values.get(transfer.output_variable)
            if value is None:
                operation = node.named_children[0] if node.type == "expression_statement" and len(node.named_children) == 1 else node
                candidate = field(operation, "right") or field(operation, "value")
                value = candidate if candidate is not None and len(statement.definitions) == 1 else None
            if value is not None and value.type == "expression_list" and len(value.named_children) == 1:
                value = value.named_children[0]
            expression = self.operand(value) if self.operator(value) == "&" else value
            path = self.collector.resolve_access_path(expression) if expression is not None else None
            source = path.render() if path is not None and all(part.isidentifier() or part == "*" for part in path.render().split(".")) else ""
            mode = ""
            if source:
                raw = self.type_at(transfer.output_variable, node) or self.infer(value, node)
                mode = "address" if self.operator(value) == "&" else "reference" if "*" in raw or "&" in raw else "value_copy"
            transfers.append(replace(transfer, reference_source=source, storage_mode=mode))
        transfers.extend(extras)
        return replace(statement, storage_allocations=tuple(allocations), value_transfers=tuple(transfers),
            definitions=tuple(dict.fromkeys((*statement.definitions, *(item.output_variable for item in extras)))),
            uses=tuple(dict.fromkeys((*statement.uses, *(name for item in extras for name in item.input_variables)))))
