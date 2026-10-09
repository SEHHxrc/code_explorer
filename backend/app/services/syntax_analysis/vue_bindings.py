"""Vue setup 模板的有限读写绑定，程序图与安全前端共享同一词法身份。"""

from __future__ import annotations

from typing import Any

from .javascript import javascript_access_name
from .source_units import SourceUnit, TemplateExpression
from .tree_parser import field, text


def vue_setup_bindings(
    root: Any, source: bytes
) -> tuple[frozenset[str], frozenset[str]]:
    """返回顶层可写标量和可观察 Vue ref；仅识别静态 ESM 导入及直接调用。

    输入 setup 脚本 AST 与源码，输出普通 let/var 名字和 Vue ref 名字。
    不把 const 标量、未知工厂、自动导入或嵌套局部变量当成模板可写状态。
    """
    factories: set[str] = set()
    declarations: list[Any] = []
    writes: set[str] = set()
    for statement in root.named_children:
        if (
            statement.type == "import_statement"
            and text(field(statement, "source"), source).strip("'\"") == "vue"
        ):
            pending = list(statement.named_children)
            while pending:
                node = pending.pop()
                if node.type == "import_specifier" and text(
                    field(node, "name"), source
                ) in {"ref", "shallowRef"}:
                    factories.add(
                        text(field(node, "alias") or field(node, "name"), source)
                    )
                elif node.type == "namespace_import":
                    name = next(
                        (
                            child
                            for child in node.named_children
                            if child.type == "identifier"
                        ),
                        None,
                    )
                    factories.update(
                        text(name, source) + "." + kind
                        for kind in ("ref", "shallowRef")
                    )
                pending.extend(node.named_children)
        elif statement.type in {"lexical_declaration", "variable_declaration"}:
            declarations.extend(
                child
                for child in statement.named_children
                if child.type == "variable_declarator"
            )
        elif statement.type == "expression_statement":
            for child in statement.named_children:
                if child.type == "assignment_expression":
                    writes.add(text(field(child, "left"), source))
    mutable, refs = set(), set()
    for node in declarations:
        name = field(node, "name")
        if name is None or name.type != "identifier":
            continue
        raw = text(name, source)
        if text(node.parent, source).lstrip().startswith(("let ", "var ")):
            mutable.add(raw)
        value = field(node, "value")
        if (
            value is not None
            and value.type == "call_expression"
            and text(field(value, "function"), source) in factories - writes
            and raw not in writes
        ):
            refs.add(raw)
    return frozenset(mutable), frozenset(refs)


def vue_template_names(names: tuple[str, ...], refs: frozenset[str]) -> tuple[str, ...]:
    """模板 ref 自动解包为脚本侧 .value；其他词法名字保持不变。"""
    result = []
    for name in names:
        selected = next(
            (
                ref
                for ref in refs
                if name == ref or name.startswith((ref + ".", ref + "["))
            ),
            None,
        )
        result.append(selected + ".value" + name[len(selected) :] if selected else name)
    return tuple(result)


def vue_model_target(
    node: Any, source: bytes, mutable: frozenset[str], refs: frozenset[str]
) -> str:
    """返回有限支持的可写模板目标；动态键、未知对象及无绑定名字返回空串。"""
    name = javascript_access_name(node, source)
    if name in mutable or any(
        name == ref or name.startswith((ref + ".", ref + "[")) for ref in refs
    ):
        return vue_template_names((name,), refs)[0]
    return ""


def vue_template_expression_nodes(
    unit: SourceUnit, root: Any
) -> tuple[tuple[TemplateExpression, Any], ...]:
    """按原字节范围关联遮罩表达式与指令，避免把 v-model 误当 v-html。"""
    expressions = {
        (item.start_byte, item.end_byte): item for item in unit.template_expressions
    }
    result = []
    for statement in root.named_children:
        node = statement.named_children[0] if statement.named_children else None
        if (
            node is not None
            and node.type == "parenthesized_expression"
            and node.named_children
        ):
            node = node.named_children[0]
        if node is not None:
            binding = expressions.get((node.start_byte, node.end_byte))
            if binding is None:
                binding = next(
                    (
                        item
                        for item in unit.template_expressions
                        if item.start_byte <= node.start_byte
                        and node.end_byte <= item.end_byte
                    ),
                    None,
                )
            if binding is not None:
                result.append((binding, node))
    return tuple(result)
