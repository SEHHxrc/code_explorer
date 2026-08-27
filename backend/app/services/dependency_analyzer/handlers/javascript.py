# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler

class JavaScriptHandler(BaseHandler):
    """提取 JavaScript/JSX 模块、类、函数、变量和调用关系。"""
    lang = "javascript"
    self_names = frozenset({"this"})
    super_names = frozenset({"super"})
    builtin_funcs = JS_BUILTINS
    builtin_objects = JS_GLOBALS
    type_methods = JS_TYPE_METHODS
    stdlib_modules = JS_STDLIB

    def register(self):
        """注册 JavaScript Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "import_statement": self.h_import,
            "class_declaration": self.h_class,
            "class": self.h_class,
            "abstract_class_declaration": self.h_class,
            "interface_declaration": self.h_interface,
            "method_definition": self.h_method,
            "method_signature": self.h_method,
            "abstract_method_signature": self.h_method,
            "field_definition": self.h_field,
            "public_field_definition": self.h_field,
            "property_signature": self.h_field,
            "function_declaration": self.h_function,
            "generator_function_declaration": self.h_function,
            "function_expression": self.h_anon_function,
            "arrow_function": self.h_anon_function,
            "variable_declarator": self.h_declarator,
            "call_expression": self.h_call,
            "new_expression": self.h_new,
            "required_parameter": self.h_param,
            "optional_parameter": self.h_param,
            "type_alias_declaration": self.h_type_alias,
            "enum_declaration": self.h_enum,
        })

    def h_import(self, node, ctx):
        source = _field(node, "source")
        module = ctx.text(source).strip("\"'`")
        line = node.start_point[0] + 1
        clause = _first_of(node, {"import_clause"})
        if clause is None:
            ctx.add_import(module, alias=module.split("/")[-1], kind="module", line=line)
            return SKIP_CHILDREN
        for child in clause.named_children:
            if child.type == "identifier":                       # default import
                ctx.add_import(module, alias=ctx.text(child), symbol="default", kind="symbol", line=line)
            elif child.type == "namespace_import":               # import * as ns
                alias = _descend_for(child, {"identifier"}, 2)
                ctx.add_import(module, alias=ctx.text(alias), kind="module", line=line)
            elif child.type == "named_imports":
                for spec in child.named_children:
                    if spec.type != "import_specifier":
                        continue
                    real = ctx.text(_field(spec, "name"))
                    alias_node = _field(spec, "alias")
                    alias = ctx.text(alias_node) if alias_node is not None else real
                    ctx.add_import(module, alias=alias, symbol=real, kind="symbol", line=line)
        return SKIP_CHILDREN

    def _heritage(self, node, ctx):
        bases, impls = [], []
        for child in node.named_children:
            if child.type != "class_heritage":
                continue
            for part in child.named_children:
                if part.type == "extends_clause":
                    for target in part.named_children:
                        if target.type in ("identifier", "type_identifier", "member_expression",
                                           "generic_type", "nested_type_identifier"):
                            bases.append(ctx.text(target))
                elif part.type == "implements_clause":
                    for target in part.named_children:
                        impls.append(ctx.text(target))
                elif part.type in ("identifier", "type_identifier", "member_expression"):
                    bases.append(ctx.text(part))
        return bases, impls

    def h_class(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        if not name:
            parent = node.parent
            if parent is not None and parent.type == "variable_declarator":
                name = ctx.text(_field(parent, "name"))
        bases, impls = self._heritage(node, ctx)
        definition = ctx.add_def(node, name, "class", name_node=name_node or node, bases=bases, implements=impls)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_interface(self, node, ctx):
        name_node = _field(node, "name")
        bases = []
        for child in node.named_children:
            if child.type in ("extends_type_clause", "class_heritage"):
                for target in child.named_children:
                    if target.type in ("type_identifier", "identifier", "generic_type", "nested_type_identifier"):
                        bases.append(ctx.text(target))
        definition = ctx.add_def(node, ctx.text(name_node), "interface", name_node=name_node, bases=bases)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", definition.name, definition)
        return 0

    def h_type_alias(self, node, ctx):
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "type", name_node=name_node)
        return SKIP_CHILDREN

    def h_enum(self, node, ctx):
        name_node = _field(node, "name")
        definition = ctx.add_def(node, ctx.text(name_node), "enum", name_node=name_node)
        if definition is None:
            return SKIP_CHILDREN
        ctx.push(definition.fqn, "class", definition.name, definition)
        return 0

    def h_method(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        kind = "constructor" if name == "constructor" else "method"
        definition = ctx.add_def(node, name, kind, name_node=name_node,
                                 return_type=self._annotation_type(ctx, _field(node, "return_type")))
        if definition is None:
            return 0
        class_frame = ctx.enclosing_class_frame()
        frame = ctx.push(definition.fqn, "method", name, definition)
        if class_frame is not None:
            frame.owner_literal = class_frame.name
            ctx.bind_frame_var(frame, "this", class_frame.name)
        return 0

    def h_field(self, node, ctx):
        if ctx.top.kind != "class":
            return 0
        name_node = _field(node, "name") or _field(node, "property")
        name = ctx.text(name_node)
        type_literal = self._annotation_type(ctx, _field(node, "type"))
        if not type_literal:
            type_literal = self._infer_type(ctx, _field(node, "value"))
        ctx.add_def(node, name, "field", name_node=name_node or node, type_literal=type_literal)
        return 0

    @staticmethod
    def _annotation_type(ctx, type_node) -> str:
        """从 ``: Foo`` / ``: Foo<Bar>`` 类型标注里取主类型名。"""
        if type_node is None:
            return ""
        inner = _first_of(type_node, {"type_identifier", "generic_type", "nested_type_identifier",
                                      "predefined_type"})
        return ctx.text(inner or type_node)

    def h_function(self, node, ctx):
        name_node = _field(node, "name")
        definition = ctx.add_def(node, ctx.text(name_node), "function", name_node=name_node,
                                 return_type=self._annotation_type(ctx, _field(node, "return_type")))
        if definition is None:
            return 0
        ctx.push(definition.fqn, "function", definition.name, definition)
        return 0

    def h_anon_function(self, node, ctx):
        # 匿名函数：不建节点，但要建一个作用域帧，避免局部变量污染上层
        ctx.push(ctx.top.fqn, "function", "")
        return 0

    def h_param(self, node, ctx):
        pattern = _field(node, "pattern")
        type_node = _field(node, "type")
        if pattern is not None and type_node is not None:
            ctx.set_var_type(ctx.text(pattern), self._annotation_type(ctx, type_node))
        return 0

    def h_declarator(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        value = _field(node, "value")
        if value is not None and value.type in ("arrow_function", "function_expression"):
            kind = "method" if ctx.top.kind == "class" else "function"
            definition = ctx.add_def(node, name, kind, name_node=name_node or node,
                                     return_type=self._annotation_type(ctx, _field(value, "return_type")))
            if definition is not None:
                ctx.push(definition.fqn, kind, name, definition)
            return 0
        # CommonJS: const x = require('...')
        if value is not None and value.type == "call_expression":
            func = _field(value, "function")
            if func is not None and ctx.text(func) == "require":
                args = _field(value, "arguments")
                target = _descend_for(args, {"string"}, 2) if args is not None else None
                module = ctx.text(target).strip("\"'`") if target is not None else ""
                if module:
                    ctx.add_import(module, alias=name, kind="module", line=node.start_point[0] + 1)
                    return SKIP_CHILDREN
        type_literal = self._annotation_type(ctx, _field(node, "type"))
        if not type_literal:
            type_literal = self._infer_type(ctx, value)
        if ctx.in_callable():
            ctx.set_var_type(name, type_literal)
        elif ctx.top.kind == "class":
            ctx.add_def(node, name, "field", name_node=name_node or node, type_literal=type_literal)
        elif _is_meaningful_name(name):
            ctx.add_def(node, name, "constant", name_node=name_node or node, type_literal=type_literal)
        return 0

    @staticmethod
    def _infer_type(ctx, value) -> str:
        if value is None:
            return ""
        if value.type == "new_expression":
            return ctx.text(_field(value, "constructor"))
        if value.type in ("call_expression", "await_expression"):
            if value.type == "await_expression":
                value = value.named_children[0] if value.named_children else None
                if value is None or value.type != "call_expression":
                    return ""
            func = _field(value, "function")
            if func is not None and func.type in ("identifier", "member_expression"):
                return CALL_TYPE_PREFIX + ctx.text(func)
        return ""

    def h_call(self, node, ctx):
        name, receiver = self.split_callee(ctx, _field(node, "function"))
        if name:
            ctx.add_ref(node, "call", name, receiver)
        return 0

    def h_new(self, node, ctx):
        ctor = _field(node, "constructor")
        if ctor is not None:
            literal = ctx.text(ctor)
            parts = _split_qualified(literal)
            if parts:
                ctx.add_ref(node, "new", parts[-1], ".".join(parts[:-1]))
        return 0

class TypeScriptHandler(JavaScriptHandler):
    """扩展 JavaScript 处理规则，提取 TypeScript 类型、接口和注解。"""
    lang = "typescript"
