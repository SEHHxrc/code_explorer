# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler

class PythonHandler(BaseHandler):
    """提取 Python 导入、类、函数、赋值、类型和调用关系。"""
    lang = "python"
    self_names = frozenset({"self", "cls"})
    super_names = frozenset({"super"})
    builtin_funcs = PY_BUILTINS
    builtin_objects = frozenset()
    type_methods = PY_TYPE_METHODS
    stdlib_modules = PY_STDLIB

    def register(self):
        """注册 Python Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "import_statement": self.h_import,
            "import_from_statement": self.h_import_from,
            "class_definition": self.h_class,
            "function_definition": self.h_function,
            "assignment": self.h_assignment,
            "call": self.h_call,
            "typed_parameter": self.h_typed_param,
            "default_parameter": self.h_typed_param,
            "typed_default_parameter": self.h_typed_param,
        })

    # import a.b / import a.b as c
    def h_import(self, node, ctx):
        for child in node.named_children:
            if child.type == "dotted_name":
                module = ctx.text(child)
                ctx.add_import(module, alias=module.split(".")[0], kind="module", line=node.start_point[0] + 1)
            elif child.type == "aliased_import":
                module = ctx.text(_field(child, "name"))
                alias = ctx.text(_field(child, "alias"))
                ctx.add_import(module, alias=alias, kind="module", line=node.start_point[0] + 1)
        return SKIP_CHILDREN

    # from x import a as b / from . import x / from x import *
    def h_import_from(self, node, ctx):
        mod_node = _field(node, "module_name") or _field(node, "module")
        module = ctx.text(mod_node)
        line = node.start_point[0] + 1
        found = False
        for child in _fields(node, "name"):
            found = True
            if child.type == "aliased_import":
                real = ctx.text(_field(child, "name"))
                alias = ctx.text(_field(child, "alias"))
                ctx.add_import(module, alias=alias, symbol=real, kind="symbol", line=line)
            else:
                real = ctx.text(child)
                ctx.add_import(module, alias=real, symbol=real, kind="symbol", line=line)
        if _first_of(node, {"wildcard_import"}) is not None:
            ctx.add_import(module, alias="*", symbol="*", kind="wildcard", line=line)
            found = True
        if not found and module:
            ctx.add_import(module, alias=module.split(".")[-1], kind="module", line=line)
        return SKIP_CHILDREN

    def h_class(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        bases = []
        supers = _field(node, "superclasses")
        if supers is not None:
            for base in supers.named_children:
                if base.type in ("identifier", "attribute", "subscript"):
                    bases.append(ctx.text(base))
        definition = ctx.add_def(node, name, "class", name_node=name_node, bases=bases)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_function(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        in_class = ctx.top.kind == "class"
        kind = "method" if in_class else "function"
        definition = ctx.add_def(node, name, kind, name_node=name_node,
                                 return_type=ctx.text(_field(node, "return_type")))
        if definition is None:
            return 0
        class_frame = ctx.top if in_class else None
        frame = ctx.push(definition.fqn, kind, name, definition)
        if class_frame is not None:
            frame.owner_literal = class_frame.name
            params = _field(node, "parameters")
            first = params.named_children[0] if (params is not None and params.named_children) else None
            if first is not None:
                first_name = ctx.text(first if first.type == "identifier" else _descend_for(first, {"identifier"}, 2))
                if first_name in self.self_names:
                    ctx.bind_frame_var(frame, first_name, class_frame.name)
        return 0

    def h_typed_param(self, node, ctx):
        name_node = _descend_for(node, {"identifier"}, 2)
        type_node = _field(node, "type")
        if name_node is not None and type_node is not None:
            ctx.set_var_type(ctx.text(name_node), ctx.text(type_node))
        return 0

    def h_assignment(self, node, ctx):
        left = _field(node, "left")
        right = _field(node, "right")
        type_node = _field(node, "type")
        if left is None:
            return 0
        rhs_type = self._infer_type(ctx, right) or ctx.text(type_node)
        scope = ctx.top.kind

        if left.type == "identifier":
            name = ctx.text(left)
            if scope == "module":
                if _is_meaningful_name(name):
                    ctx.add_def(node, name, "constant", name_node=left, type_literal=rhs_type)
            elif scope == "class":
                if _is_meaningful_name(name):
                    ctx.add_def(node, name, "field", name_node=left, type_literal=rhs_type)
            else:
                ctx.set_var_type(name, rhs_type)
        elif left.type == "attribute":
            obj = ctx.text(_field(left, "object"))
            attr = ctx.text(_field(left, "attribute"))
            class_frame = ctx.enclosing_class_frame()
            if obj in self.self_names and class_frame is not None and attr:
                ctx.add_def(node, attr, "field", name_node=_field(left, "attribute"),
                            type_literal=rhs_type, parent_override=class_frame.fqn)
        return 0

    def _infer_type(self, ctx, node) -> str:
        """从右值推断类型：``Base()`` / ``get_user()`` 统一记成待回填的调用类型。"""
        if node is None:
            return ""
        if node.type == "call":
            func = _field(node, "function")
            if func is not None and func.type in ("identifier", "attribute"):
                return CALL_TYPE_PREFIX + ctx.text(func)
        return ""

    def h_call(self, node, ctx):
        name, receiver = self.split_callee(ctx, _field(node, "function"))
        if receiver.endswith("()"):
            receiver = receiver[:-2]        # super().foo() -> 接收者视作 super
        if name:
            ctx.add_ref(node, "call", name, receiver)
        return 0
