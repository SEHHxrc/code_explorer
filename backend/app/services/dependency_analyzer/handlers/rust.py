# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler

class RustHandler(BaseHandler):
    """提取 Rust 模块、use、结构体、trait、impl、函数和调用关系。"""
    lang = "rust"
    self_names = frozenset({"self", "Self"})
    super_names = frozenset()
    builtin_funcs = RUST_BUILTINS
    builtin_objects = RUST_STD_TYPES
    type_methods = RUST_TYPE_METHODS
    stdlib_modules = RUST_STD_ROOTS
    bare_call_hits_class = False

    def register(self):
        """注册 Rust Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "use_declaration": self.h_use,
            "mod_item": self.h_mod,
            "struct_item": self.h_record,
            "enum_item": self.h_record,
            "union_item": self.h_record,
            "trait_item": self.h_record,
            "type_item": self.h_type_alias,
            "impl_item": self.h_impl,
            "function_item": self.h_function,
            "function_signature_item": self.h_function,
            "field_declaration": self.h_field,
            "enum_variant": self.h_variant,
            "const_item": self.h_const,
            "static_item": self.h_const,
            "let_declaration": self.h_let,
            "parameter": self.h_param,
            "call_expression": self.h_call,
            "macro_invocation": self.h_macro,
            "struct_expression": self.h_struct_expr,
        })

    def h_use(self, node, ctx):
        argument = _field(node, "argument")
        line = node.start_point[0] + 1
        for module, alias, symbol, kind in self._flatten_use(ctx, argument, ""):
            ctx.add_import(module, alias=alias, symbol=symbol, kind=kind, line=line)
        return SKIP_CHILDREN

    def _flatten_use(self, ctx, node, prefix) -> list:
        out = []
        if node is None:
            return out
        node_type = node.type
        if node_type == "scoped_identifier":
            path = ctx.text(_field(node, "path"))
            name = ctx.text(_field(node, "name"))
            full = "::".join(p for p in (prefix, path) if p)
            out.append((full, name, name, "symbol"))
        elif node_type == "identifier":
            name = ctx.text(node)
            out.append((prefix or name, name, name if prefix else "", "symbol" if prefix else "module"))
        elif node_type == "use_as_clause":
            path_node = _field(node, "path")
            alias = ctx.text(_field(node, "alias"))
            parts = _split_qualified(ctx.text(path_node).replace("::", "."))
            module = "::".join(([prefix] if prefix else []) + parts[:-1])
            out.append((module or (parts[0] if parts else ""), alias, parts[-1] if parts else "", "symbol"))
        elif node_type == "scoped_use_list":
            path = ctx.text(_field(node, "path"))
            full = "::".join(p for p in (prefix, path) if p)
            use_list = _field(node, "list")
            if use_list is not None:
                for child in use_list.named_children:
                    out.extend(self._flatten_use(ctx, child, full))
        elif node_type == "use_list":
            for child in node.named_children:
                out.extend(self._flatten_use(ctx, child, prefix))
        elif node_type == "use_wildcard":
            path = ctx.text(node).replace("::*", "")
            out.append(("::".join(p for p in (prefix, path) if p), "*", "*", "wildcard"))
        return out

    def h_mod(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        body = _field(node, "body")
        if body is None:
            ctx.add_import(name, alias=name, kind="submodule", line=node.start_point[0] + 1)
            return SKIP_CHILDREN
        definition = ctx.add_def(node, name, "namespace", name_node=name_node)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "namespace", name, definition)
        return 0

    def h_record(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        kind = {"struct_item": "struct", "enum_item": "enum",
                "union_item": "union", "trait_item": "trait"}[node.type]
        bases = []
        if node.type == "trait_item":
            bounds = _field(node, "bounds")
            if bounds is not None:
                for child in bounds.named_children:
                    if child.type in ("type_identifier", "scoped_type_identifier"):
                        bases.append(_norm_type(ctx.text(child)))
        definition = ctx.add_def(node, name, kind, name_node=name_node, bases=bases)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_type_alias(self, node, ctx):
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "type", name_node=name_node,
                    bases=[_norm_type(ctx.text(_field(node, "type")))])
        return SKIP_CHILDREN

    def h_impl(self, node, ctx):
        type_literal = _norm_type(ctx.text(_field(node, "type")))
        trait_literal = _norm_type(ctx.text(_field(node, "trait")))
        if not type_literal:
            return 0
        # impl 块本身不建节点：方法在阶段三挂接到真正的类型定义上
        frame = ctx.push(f"{ctx.path}::{type_literal}", "impl", type_literal, None, owner_literal=type_literal)
        ctx.bind_frame_var(frame, "self", type_literal)
        ctx.bind_frame_var(frame, "Self", type_literal)
        if trait_literal:
            ctx.refs.append(Reference(file=ctx.path, from_fqn=f"{ctx.path}::{type_literal}", kind="implements",
                                      name=trait_literal, line=node.start_point[0] + 1,
                                      class_fqn=f"{ctx.path}::{type_literal}", lang=ctx.lang))
        return 0

    def h_function(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        frame_kind = ctx.top.kind
        owner = ctx.top.owner_literal or (ctx.top.name if frame_kind == "class" else "")
        kind = "method" if frame_kind in ("class", "impl") else "function"
        definition = ctx.add_def(node, name, kind, name_node=name_node, owner_literal=owner,
                                 is_declaration=node.type == "function_signature_item",
                                 return_type=ctx.text(_field(node, "return_type")))
        if definition is None:
            return 0
        frame = ctx.push(definition.fqn, kind, name, definition, owner_literal=owner)
        if owner:
            ctx.bind_frame_var(frame, "self", owner)
            ctx.bind_frame_var(frame, "Self", owner)
        return 0

    def h_field(self, node, ctx):
        if ctx.top.kind != "class":
            return 0
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "field", name_node=name_node,
                    type_literal=ctx.text(_field(node, "type")))
        return SKIP_CHILDREN

    def h_variant(self, node, ctx):
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "constant", name_node=name_node)
        return SKIP_CHILDREN

    def h_const(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        if ctx.in_callable():
            ctx.set_var_type(name, ctx.text(_field(node, "type")))
        else:
            ctx.add_def(node, name, "constant", name_node=name_node, type_literal=ctx.text(_field(node, "type")))
        return 0

    def h_let(self, node, ctx):
        pattern = _field(node, "pattern")
        name = ctx.text(pattern) if pattern is not None and pattern.type == "identifier" else ""
        type_literal = ctx.text(_field(node, "type")) or self._infer_type(ctx, _field(node, "value"))
        if name:
            ctx.set_var_type(name, type_literal)
        return 0

    def h_param(self, node, ctx):
        pattern = _field(node, "pattern")
        if pattern is not None and pattern.type == "identifier":
            ctx.set_var_type(ctx.text(pattern), ctx.text(_field(node, "type")))
        return 0

    def _infer_type(self, ctx, value) -> str:
        if value is None:
            return ""
        if value.type == "struct_expression":
            return _norm_type(ctx.text(_field(value, "name")))
        if value.type == "call_expression":
            func = _field(value, "function")
            if func is None:
                return ""
            if func.type == "scoped_identifier":
                path = _norm_type(ctx.text(_field(func, "path")))
                name = ctx.text(_field(func, "name"))
                if name in ("new", "default", "from", "with_capacity", "build"):
                    return path
            return CALL_TYPE_PREFIX + ctx.text(func).replace("::", ".")
        if value.type == "reference_expression":
            return self._infer_type(ctx, _field(value, "value"))
        return ""

    def h_call(self, node, ctx):
        func = _field(node, "function")
        if func is None:
            return 0
        if func.type == "scoped_identifier":
            path = ctx.text(_field(func, "path"))
            name = ctx.text(_field(func, "name"))
            ctx.add_ref(node, "call", name, path)
        else:
            name, receiver = self.split_callee(ctx, func)
            if name:
                ctx.add_ref(node, "call", name, receiver)
        return 0

    def h_macro(self, node, ctx):
        macro = _field(node, "macro")
        name = ctx.text(macro)
        if name:
            ctx.add_ref(node, "call", name.split("::")[-1], "")
        return 0

    def h_struct_expr(self, node, ctx):
        name_node = _field(node, "name")
        if name_node is not None:
            parts = _split_qualified(ctx.text(name_node).replace("::", "."))
            if parts:
                ctx.add_ref(node, "new", parts[-1], ".".join(parts[:-1]))
        return 0
