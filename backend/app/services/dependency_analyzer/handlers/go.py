# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler

class GoHandler(BaseHandler):
    """提取 Go 包、导入、类型、函数、方法、接收者和调用关系。"""
    lang = "go"
    self_names = frozenset()
    super_names = frozenset()
    builtin_funcs = GO_BUILTINS
    stdlib_modules = GO_STDLIB
    bare_call_hits_class = False

    def register(self):
        """注册 Go Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "package_clause": self.h_package,
            "import_declaration": self.h_import,
            "type_spec": self.h_type_spec,
            "type_alias": self.h_type_spec,
            "field_declaration": self.h_field,
            "method_elem": self.h_method_elem,
            "method_declaration": self.h_method,
            "function_declaration": self.h_function,
            "func_literal": self.h_func_literal,
            "const_spec": self.h_var_spec,
            "var_spec": self.h_var_spec,
            "short_var_declaration": self.h_short_var,
            "parameter_declaration": self.h_param,
            "call_expression": self.h_call,
            "composite_literal": self.h_composite,
        })

    def h_package(self, node, ctx):
        ident = _first_of(node, {"package_identifier"})
        ctx.package = ctx.text(ident)
        return SKIP_CHILDREN

    def h_import(self, node, ctx):
        line = node.start_point[0] + 1
        specs = []
        for child in node.named_children:
            if child.type == "import_spec":
                specs.append(child)
            elif child.type == "import_spec_list":
                specs.extend(c for c in child.named_children if c.type == "import_spec")
        for spec in specs:
            path_node = _field(spec, "path")
            module = ctx.text(path_node).strip("\"`")
            alias_node = _field(spec, "name")
            alias = ctx.text(alias_node) if alias_node is not None else module.split("/")[-1]
            ctx.add_import(module, alias=alias, kind="module", line=line)
        return SKIP_CHILDREN

    def h_type_spec(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        type_node = _field(node, "type")
        if type_node is None:
            return 0
        if type_node.type == "struct_type":
            kind = "struct"
        elif type_node.type == "interface_type":
            kind = "interface"
        else:
            # 类型别名：把底层类型登记成“基类”，便于成员提升
            ctx.add_def(node, name, "type", name_node=name_node, bases=[_norm_type(ctx.text(type_node))])
            return SKIP_CHILDREN
        definition = ctx.add_def(node, name, kind, name_node=name_node)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_field(self, node, ctx):
        frame = ctx.top
        if frame.kind != "class" or frame.definition is None:
            return 0
        name_nodes = _fields(node, "name")
        type_node = _field(node, "type")
        type_literal = ctx.text(type_node)
        if not name_nodes:
            # 匿名嵌入字段 => Go 的组合式“继承”，成员会被提升
            embedded = _norm_type(type_literal)
            if embedded and embedded not in frame.definition.bases:
                frame.definition.bases.append(embedded)
            return SKIP_CHILDREN
        for name_node in name_nodes:
            ctx.add_def(node, ctx.text(name_node), "field", name_node=name_node, type_literal=type_literal)
        return SKIP_CHILDREN

    def h_method_elem(self, node, ctx):
        """接口里的方法声明。"""
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "method", name_node=name_node, is_declaration=True)
        return SKIP_CHILDREN

    def h_method(self, node, ctx):
        receiver = _field(node, "receiver")
        recv_var, recv_type = "", ""
        if receiver is not None:
            param = _first_of(receiver, {"parameter_declaration"})
            if param is not None:
                recv_var = ctx.text(_field(param, "name"))
                recv_type = _norm_type(ctx.text(_field(param, "type")))
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        # 方法归属的类型可能定义在同包的另一个文件里 -> 先用虚拟前缀占位，阶段三再挂接
        parent = f"{ctx.path}::{recv_type}" if recv_type else ctx.path
        definition = ctx.add_def(node, name, "method", name_node=name_node,
                                 owner_literal=recv_type, parent_override=parent,
                                 return_type=ctx.text(_field(node, "result")))
        if definition is None:
            return 0
        frame = ctx.push(definition.fqn, "method", name, definition, owner_literal=recv_type)
        if recv_var and recv_type:
            ctx.bind_frame_var(frame, recv_var, recv_type)
        return 0

    def h_function(self, node, ctx):
        name_node = _field(node, "name")
        definition = ctx.add_def(node, ctx.text(name_node), "function", name_node=name_node,
                                 return_type=ctx.text(_field(node, "result")))
        if definition is None:
            return 0
        ctx.push(definition.fqn, "function", definition.name, definition)
        return 0

    def h_func_literal(self, node, ctx):
        ctx.push(ctx.top.fqn, "function", "")
        return 0

    def h_var_spec(self, node, ctx):
        kind = "constant" if node.type == "const_spec" else "variable"
        type_node = _field(node, "type")
        type_literal = ctx.text(type_node) if type_node is not None else self._infer_type(ctx, _field(node, "value"))
        for name_node in _fields(node, "name"):
            name = ctx.text(name_node)
            if ctx.in_callable():
                ctx.set_var_type(name, type_literal)
            elif _is_meaningful_name(name):
                ctx.add_def(node, name, kind, name_node=name_node, type_literal=type_literal)
        return 0

    def h_short_var(self, node, ctx):
        left = _field(node, "left")
        right = _field(node, "right")
        if left is None or right is None:
            return 0
        names = [c for c in left.named_children if c.type == "identifier"]
        values = list(right.named_children)
        for idx, name_node in enumerate(names):
            value = values[idx] if idx < len(values) else (values[0] if values else None)
            ctx.set_var_type(ctx.text(name_node), self._infer_type(ctx, value))
        return 0

    def h_param(self, node, ctx):
        type_literal = ctx.text(_field(node, "type"))
        for name_node in _fields(node, "name"):
            ctx.set_var_type(ctx.text(name_node), type_literal)
        return 0

    def _infer_type(self, ctx, value) -> str:
        if value is None:
            return ""
        if value.type == "expression_list":
            value = value.named_children[0] if value.named_children else None
            if value is None:
                return ""
        if value.type == "unary_expression":
            value = _field(value, "operand")
            if value is None:
                return ""
        if value.type == "composite_literal":
            return _norm_type(ctx.text(_field(value, "type")))
        if value.type == "call_expression":
            func = _field(value, "function")
            if func is None:
                return ""
            if func.type == "identifier" and ctx.text(func) == "new":   # new(Animal)
                args = _field(value, "arguments")
                inner = _first_of(args, {"type_identifier", "identifier"}) if args is not None else None
                return _norm_type(ctx.text(inner))
            return CALL_TYPE_PREFIX + ctx.text(func)
        return ""

    def h_call(self, node, ctx):
        name, receiver = self.split_callee(ctx, _field(node, "function"))
        if name:
            ctx.add_ref(node, "call", name, receiver)
        return 0

    def h_composite(self, node, ctx):
        type_node = _field(node, "type")
        if type_node is not None and type_node.type in ("type_identifier", "qualified_type"):
            literal = ctx.text(type_node)
            parts = _split_qualified(literal.replace("/", "."))
            if parts:
                ctx.add_ref(node, "new", parts[-1], ".".join(parts[:-1]))
        return 0
