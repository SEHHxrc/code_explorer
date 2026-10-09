"""混合文件的共享源码视图；原字节偏移不变，不包含安全规则或图模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tree_parser import DEFAULT_TREE_SITTER_PARSER_POOL, text


@dataclass(frozen=True)
class TemplateExpression:
    """模板指令与原文件表达式范围；指令方向由消费者决定，不包含安全判定。"""

    directive: str
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class SourceUnit:
    """脚本与模板表达式视图；遮罩保留原文件行列，供所有静态前端复用。"""

    language: str
    source: bytes
    template_source: bytes = b""
    setup: bool = False
    diagnostics: tuple[str, ...] = ()
    template_expressions: tuple[TemplateExpression, ...] = ()


def _mask(source: bytes) -> bytearray:
    """只将非换行字节替换为空格，保留 UTF-8 字节坐标和 CRLF。"""
    return bytearray(value if value in {10, 13} else 32 for value in source)


def _children(root: Any, kind: str) -> list[Any]:
    """读取标签的直接属性，避免嵌套节点误归属。"""
    return [child for child in root.named_children if child.type == kind]


def source_unit(path: str, source: bytes, language: str) -> SourceUnit:
    """普通文件原样返回；Vue 保留内联脚本、v-html 和原生文本 v-model 坐标。"""
    if not path.lower().endswith(".vue"):
        return SourceUnit(language, source)
    root = DEFAULT_TREE_SITTER_PARSER_POOL.get("html").parse(source).root_node
    scripts, templates = _mask(source), _mask(source)
    diagnostics: list[str] = []
    expressions: list[TemplateExpression] = []
    setup, script_count, template_count = False, 0, 0
    selected_language = "javascript"
    pending = [(root, False, False)]
    while pending:
        node, in_template, scoped = pending.pop()
        if node.type == "element":
            tag = next(iter(_children(node, "start_tag")), None)
            name = (
                next(iter(_children(tag, "tag_name")), None)
                if tag is not None
                else None
            )
            if text(name, source) == "template":
                in_template = True
            if tag is not None:
                scoped = scoped or any(
                    text(
                        next(iter(_children(attr, "attribute_name")), None), source
                    ).startswith(("v-for", "v-slot", "#"))
                    for attr in _children(tag, "attribute")
                )
        if node.type == "script_element":
            script_count += 1
            tag = next(iter(_children(node, "start_tag")), None)
            attrs = {}
            for attr in _children(tag, "attribute") if tag is not None else []:
                key = next(iter(_children(attr, "attribute_name")), None)
                quoted = next(iter(_children(attr, "quoted_attribute_value")), None)
                attrs[text(key, source)] = text(quoted, source).strip("'\"")
            lang = attrs.get("lang", "js")
            if lang not in {"js", "javascript", "ts", "typescript"} or "src" in attrs:
                diagnostics.append("vue_external_or_unsupported_script")
                continue
            selected_language = (
                "typescript" if lang in {"ts", "typescript"} else "javascript"
            )
            setup = setup or "setup" in attrs
            for raw in _children(node, "raw_text"):
                scripts[raw.start_byte : raw.end_byte] = source[
                    raw.start_byte : raw.end_byte
                ]
        if in_template and node.type == "attribute":
            key = next(iter(_children(node, "attribute_name")), None)
            directive = text(key, source)
            is_model = directive == "v-model" or directive.startswith("v-model.")
            if directive == "v-html" or is_model:
                tag = node.parent
                name = (
                    next(iter(_children(tag, "tag_name")), None)
                    if tag is not None
                    else None
                )
                tag_scoped = scoped or any(
                    text(
                        next(iter(_children(attr, "attribute_name")), None), source
                    ).startswith(("v-for", "v-slot", "#"))
                    for attr in _children(tag, "attribute")
                    if tag is not None
                )
                if tag_scoped:
                    diagnostics.append("vue_template_scoped_binding_not_modeled")
                    continue
                if is_model and text(name, source) not in {"input", "textarea"}:
                    diagnostics.append("vue_custom_or_select_v_model_not_modeled")
                    continue
                if is_model and text(name, source) == "input":
                    type_attrs = [
                        attr
                        for attr in _children(tag, "attribute")
                        if text(
                            next(iter(_children(attr, "attribute_name")), None), source
                        )
                        in {"type", ":type", "v-bind:type"}
                    ]
                    if type_attrs and (
                        len(type_attrs) != 1
                        or text(
                            next(
                                iter(
                                    _children(type_attrs[0], "quoted_attribute_value")
                                ),
                                None,
                            ),
                            source,
                        ).strip("'\"")
                        not in {"text", "password", "email", "search", "url", "tel"}
                    ):
                        diagnostics.append(
                            "vue_non_text_or_dynamic_v_model_not_modeled"
                        )
                        continue
                quoted = next(iter(_children(node, "quoted_attribute_value")), None)
                value = (
                    next(iter(_children(quoted, "attribute_value")), None)
                    if quoted is not None
                    else None
                )
                if (
                    value is not None
                    and template_count < 128
                    and b"&" not in source[value.start_byte : value.end_byte]
                ):
                    template_count += 1
                    expressions.append(
                        TemplateExpression(directive, value.start_byte, value.end_byte)
                    )
                    templates[value.start_byte - 1] = 40
                    templates[value.start_byte : value.end_byte] = source[
                        value.start_byte : value.end_byte
                    ]
                    templates[value.end_byte] = 41
                    if value.end_byte + 1 < len(templates):
                        templates[value.end_byte + 1] = 59
                else:
                    diagnostics.append(
                        "vue_template_expression_unsupported_or_budget_exceeded"
                    )
        pending.extend(
            (child, in_template, scoped) for child in reversed(node.named_children)
        )
    if root.has_error:
        diagnostics.append("vue_partial_html_parse_error")
    if script_count > 1:
        diagnostics.append("vue_multiple_script_blocks_not_combined_semantically")
        setup = False
    if not setup:
        diagnostics.append("vue_options_api_and_template_scope_not_modeled")
    return SourceUnit(
        selected_language,
        bytes(scripts),
        bytes(templates) if template_count else b"",
        setup,
        tuple(dict.fromkeys(diagnostics)),
        tuple(expressions),
    )
