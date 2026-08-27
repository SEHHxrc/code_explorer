# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler

class JavaHandler(BaseHandler):
    """提取 Java 包、导入、类、接口、方法、构造器和调用关系。"""
    lang = "java"
    self_names = frozenset({"this"})
    super_names = frozenset({"super"})
    builtin_funcs = JAVA_BUILTINS
    builtin_objects = frozenset({"System", "Math", "String", "Integer", "Double", "Long",
                                 "Boolean", "Character", "Objects", "Arrays", "Collections"})
    type_methods = JAVA_TYPE_METHODS
    stdlib_modules = frozenset({"java", "javax", "sun", "jdk"})

    def register(self):
        """注册 Java Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "package_declaration": self.h_package,
            "import_declaration": self.h_import,
            "class_declaration": self.h_class,
            "interface_declaration": self.h_class,
            "enum_declaration": self.h_class,
            "record_declaration": self.h_class,
            "annotation_type_declaration": self.h_class,
            "field_declaration": self.h_field,
            "constant_declaration": self.h_field,
            "method_declaration": self.h_method,
            "constructor_declaration": self.h_method,
            "enum_constant": self.h_enum_constant,
            "local_variable_declaration": self.h_local_var,
            "formal_parameter": self.h_param,
            "method_invocation": self.h_call,
            "object_creation_expression": self.h_new,
            "lambda_expression": self.h_lambda,
        })

    def h_package(self, node, ctx):
        ctx.package = ctx.text(_first_of(node, {"scoped_identifier", "identifier"}))
        return SKIP_CHILDREN

    def h_import(self, node, ctx):
        target = _first_of(node, {"scoped_identifier", "identifier"})
        literal = ctx.text(target)
        line = node.start_point[0] + 1
        is_wildcard = any(child.type == "asterisk" for child in node.named_children)
        if is_wildcard:
            ctx.add_import(literal, alias="*", symbol="*", kind="wildcard", line=line)
        else:
            simple = literal.rsplit(".", 1)[-1]
            module = literal.rsplit(".", 1)[0] if "." in literal else literal
            ctx.add_import(module, alias=simple, symbol=simple, kind="symbol", line=line)
        return SKIP_CHILDREN

    def h_class(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        bases, impls = [], []
        superclass = _field(node, "superclass")
        if superclass is not None:
            for child in superclass.named_children:
                bases.append(ctx.text(child))
        interfaces = _field(node, "interfaces")
        if interfaces is not None:
            type_list = _first_of(interfaces, {"type_list"}) or interfaces
            for child in type_list.named_children:
                impls.append(ctx.text(child))
        kind = {"interface_declaration": "interface", "enum_declaration": "enum"}.get(node.type, "class")
        definition = ctx.add_def(node, name, kind, name_node=name_node, bases=bases, implements=impls)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_field(self, node, ctx):
        type_literal = ctx.text(_field(node, "type"))
        in_class = ctx.top.kind == "class"
        for declarator in _fields(node, "declarator"):
            name_node = _field(declarator, "name")
            name = ctx.text(name_node)
            if in_class:
                ctx.add_def(node, name, "field", name_node=name_node or node, type_literal=type_literal)
            else:
                ctx.set_var_type(name, type_literal)
        return 0

    def h_enum_constant(self, node, ctx):
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "constant", name_node=name_node)
        return 0

    def h_method(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        kind = "constructor" if node.type == "constructor_declaration" else "method"
        definition = ctx.add_def(node, name, kind, name_node=name_node,
                                 return_type=ctx.text(_field(node, "type")))
        if definition is None:
            return 0
        class_frame = ctx.enclosing_class_frame()
        frame = ctx.push(definition.fqn, "method", name, definition)
        if class_frame is not None:
            frame.owner_literal = class_frame.name
            ctx.bind_frame_var(frame, "this", class_frame.name)
        return 0

    def h_lambda(self, node, ctx):
        ctx.push(ctx.top.fqn, "function", "")
        return 0

    def h_local_var(self, node, ctx):
        type_literal = ctx.text(_field(node, "type"))
        for declarator in _fields(node, "declarator"):
            name = ctx.text(_field(declarator, "name"))
            inferred = type_literal
            if inferred in ("var", ""):
                inferred = self._infer_type(ctx, _field(declarator, "value"))
            ctx.set_var_type(name, inferred)
        return 0

    def h_param(self, node, ctx):
        ctx.set_var_type(ctx.text(_field(node, "name")), ctx.text(_field(node, "type")))
        return 0

    @staticmethod
    def _infer_type(ctx, value) -> str:
        if value is None:
            return ""
        if value.type == "object_creation_expression":
            return ctx.text(_field(value, "type"))
        if value.type == "method_invocation":
            obj = _field(value, "object")
            name = ctx.text(_field(value, "name"))
            prefix = (ctx.text(obj) + ".") if obj is not None else ""
            return CALL_TYPE_PREFIX + prefix + name
        return ""

    def h_call(self, node, ctx):
        name = ctx.text(_field(node, "name"))
        obj = _field(node, "object")
        receiver = ctx.text(obj).strip() if obj is not None else ""
        if name:
            ctx.add_ref(node, "call", name, receiver)
        return 0

    def h_new(self, node, ctx):
        type_node = _field(node, "type")
        if type_node is not None:
            parts = _split_qualified(ctx.text(type_node))
            if parts:
                ctx.add_ref(node, "new", parts[-1], ".".join(parts[:-1]))
        return 0
