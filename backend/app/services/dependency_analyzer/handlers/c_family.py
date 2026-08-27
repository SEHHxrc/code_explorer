# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import defaultdict

from ..ast_utils import *
from ..constants import *
from ..context import FileContext
from ..models import Definition, Reference
from .base import BaseHandler


_DECLARATOR_WRAPPERS = frozenset({
    "pointer_declarator",
    "array_declarator",
    "parenthesized_declarator",
    "reference_declarator",
    "abstract_pointer_declarator",
    "attributed_declarator",
})
_NAME_NODES = frozenset({
    "identifier",
    "field_identifier",
    "type_identifier",
    "operator_name",
    "destructor_name",
})


def _unwrap_declarator(node):
    """拆解 C/C++ 声明符，返回 (名字节点, 是否函数, 参数节点)。"""
    current = node
    is_func = False
    params = None
    for _ in range(12):
        if current is None:
            break
        if current.type in _DECLARATOR_WRAPPERS:
            current = _field(current, "declarator") or (current.named_children[0] if current.named_children else None)
            continue
        if current.type == "function_declarator":
            is_func = True
            params = _field(current, "parameters")
            current = _field(current, "declarator")
            continue
        break
    if current is not None and current.type not in _NAME_NODES:
        current = _descend_for(current, _NAME_NODES, 3)
    return current, is_func, params

class CHandler(BaseHandler):
    """提取 C 包含、类型、函数、字段、声明和调用关系。"""
    lang = "c"
    self_names = frozenset()
    super_names = frozenset()
    builtin_funcs = C_BUILTINS
    bare_call_hits_class = False

    def register(self):
        """注册 C Tree-sitter 节点回调；无返回值。"""
        self.bind({
            "preproc_include": self.h_include,
            "preproc_def": self.h_macro,
            "preproc_function_def": self.h_macro,
            "struct_specifier": self.h_record,
            "union_specifier": self.h_record,
            "enum_specifier": self.h_record,
            "type_definition": self.h_typedef,
            "field_declaration": self.h_field,
            "declaration": self.h_declaration,
            "function_definition": self.h_function_def,
            "parameter_declaration": self.h_param,
            "enumerator": self.h_enumerator,
            "call_expression": self.h_call,
        })

    def h_include(self, node, ctx):
        path_node = _field(node, "path")
        if path_node is None:
            return SKIP_CHILDREN
        literal = ctx.text(path_node).strip()
        line = node.start_point[0] + 1
        if path_node.type == "system_lib_string":
            ctx.add_import(literal.strip("<>"), alias=literal.strip("<>"), kind="system", line=line)
        else:
            ctx.add_import(literal.strip("\""), alias=literal.strip("\""), kind="module", line=line)
        return SKIP_CHILDREN

    def h_macro(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        kind = "function" if node.type == "preproc_function_def" else "macro"
        if name and (kind == "function" or _is_meaningful_name(name)):
            ctx.add_def(node, name, kind, name_node=name_node)
        return SKIP_CHILDREN

    def h_typedef(self, node, ctx):
        """``typedef struct {...} Point;``——把别名传给随后被遍历到的结构体节点。"""
        type_node = _field(node, "type")
        alias_node = None
        for declarator in _fields(node, "declarator"):
            candidate, _is_func, _params = _unwrap_declarator(declarator)
            if candidate is not None:
                alias_node = candidate
                break
        alias = ctx.text(alias_node)
        if type_node is not None and type_node.type in ("struct_specifier", "union_specifier",
                                                        "enum_specifier", "class_specifier"):
            # 交给 h_record 建定义（保持单次遍历），这里只把别名传下去
            ctx.pending_typedef = alias
            return 0
        if alias:
            ctx.add_def(node, alias, "type", name_node=alias_node or node,
                        bases=[_norm_type(ctx.text(type_node))] if type_node is not None else [])
        return SKIP_CHILDREN

    def h_record(self, node, ctx):
        body = _field(node, "body")
        name_node = _field(node, "name")
        name = ctx.text(name_node)
        alias = ctx.pending_typedef
        ctx.pending_typedef = ""
        if body is None:
            # ``struct Node *next;`` 这类只是类型引用，不是定义
            if name:
                ctx.add_ref(node, "typeref", name)
            return SKIP_CHILDREN
        if not name:
            name = alias
            name_node = name_node or node
        if not name:
            return 0
        kind = {"struct_specifier": "struct", "union_specifier": "union",
                "enum_specifier": "enum", "class_specifier": "class"}.get(node.type, "struct")
        bases = []
        for child in node.named_children:
            if child.type == "base_class_clause":
                for base in child.named_children:
                    if base.type in ("type_identifier", "qualified_identifier", "template_type"):
                        bases.append(ctx.text(base))
        definition = ctx.add_def(node, name, kind, name_node=name_node or node, bases=bases)
        if definition is None:
            return 0
        if alias and alias != name:
            # typedef 别名与结构体标签名不同：登记成同义类型
            ctx.add_def(node, alias, "type", name_node=name_node or node, bases=[name])
        ctx.push(definition.fqn, "class", name, definition)
        return 0

    def h_enumerator(self, node, ctx):
        name_node = _field(node, "name")
        ctx.add_def(node, ctx.text(name_node), "constant", name_node=name_node)
        return SKIP_CHILDREN

    def h_field(self, node, ctx):
        if ctx.top.kind != "class":
            return 0
        type_literal = ctx.text(_field(node, "type"))
        declarators = _fields(node, "declarator")
        if not declarators:
            # 匿名联合/结构体成员，继续下探
            return 0
        for declarator in declarators:
            name_node, is_func, params = _unwrap_declarator(declarator)
            name = ctx.text(name_node)
            if not name:
                continue
            if is_func:
                # C++ 类内方法声明
                owner, simple = self._split_qualified_name(name)
                ctx.add_def(node, simple, "method", name_node=name_node, is_declaration=True,
                            owner_literal=owner, type_literal=type_literal)
            else:
                ctx.add_def(node, name, "field", name_node=name_node, type_literal=type_literal)
        return SKIP_CHILDREN

    def h_declaration(self, node, ctx):
        type_literal = ctx.text(_field(node, "type"))
        scope = ctx.top.kind
        for declarator in _fields(node, "declarator"):
            name_node, is_func, _params = _unwrap_declarator(declarator)
            name = ctx.text(name_node)
            if not name:
                continue
            if is_func:
                owner, simple = self._split_qualified_name(name)
                if scope == "class":
                    ctx.add_def(node, simple, "method", name_node=name_node,
                                is_declaration=True, owner_literal=owner, return_type=type_literal)
                else:
                    parent = f"{ctx.path}::{owner}" if owner else ""
                    ctx.add_def(node, simple, "function", name_node=name_node, is_declaration=True,
                                owner_literal=owner, parent_override=parent, return_type=type_literal)
                continue
            if scope == "class":
                ctx.add_def(node, name, "field", name_node=name_node, type_literal=type_literal)
            elif scope in ("function", "method"):
                ctx.set_var_type(name, type_literal or self._infer_type(ctx, declarator))
            elif _is_meaningful_name(name):
                ctx.add_def(node, name, "variable", name_node=name_node, type_literal=type_literal)
            # 类型出现在声明里 -> 记一次类型引用，便于建立“使用了某结构体”的边
            normalized = _norm_type(type_literal)
            if normalized and scope in ("function", "method"):
                ctx.add_ref(node, "typeref", normalized)
        return 0

    def h_function_def(self, node, ctx):
        declarator = _field(node, "declarator")
        name_node, _is_func, _params = _unwrap_declarator(declarator)
        raw_name = ctx.text(name_node)
        if not raw_name:
            return 0
        owner, simple = self._split_qualified_name(raw_name)
        in_class = ctx.top.kind == "class"
        return_type = ctx.text(_field(node, "type"))
        if in_class:
            definition = ctx.add_def(node, simple, "method", name_node=name_node, owner_literal=owner,
                                     return_type=return_type)
        elif owner:
            # C++ 类外定义：Foo::bar —— 归属在阶段三挂接到真正的类
            definition = ctx.add_def(node, simple, "method", name_node=name_node, owner_literal=owner,
                                     parent_override=f"{ctx.path}::{owner}", return_type=return_type)
        else:
            definition = ctx.add_def(node, simple, "function", name_node=name_node, return_type=return_type)
        if definition is None:
            return 0
        class_frame = ctx.top if in_class else None
        frame = ctx.push(definition.fqn, "method" if (in_class or owner) else "function", simple, definition,
                         owner_literal=owner or (class_frame.name if class_frame else ""))
        if class_frame is not None:
            frame.owner_literal = class_frame.name
            ctx.bind_frame_var(frame, "this", class_frame.name)
        elif owner:
            ctx.bind_frame_var(frame, "this", owner)
        return 0

    def h_param(self, node, ctx):
        declarator = _field(node, "declarator")
        name_node, _is_func, _params = _unwrap_declarator(declarator)
        type_literal = ctx.text(_field(node, "type"))
        if name_node is not None:
            ctx.set_var_type(ctx.text(name_node), type_literal)
        return SKIP_CHILDREN

    @staticmethod
    def _split_qualified_name(literal: str) -> tuple[str, str]:
        parts = _split_qualified(literal)
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return "", literal

    @staticmethod
    def _infer_type(ctx, declarator) -> str:
        value = _field(declarator, "value")
        if value is None:
            return ""
        if value.type == "new_expression":
            return _norm_type(ctx.text(_field(value, "type")))
        if value.type == "call_expression":
            func = _field(value, "function")
            if func is not None and func.type in ("identifier", "qualified_identifier", "field_expression"):
                return CALL_TYPE_PREFIX + ctx.text(func).replace("::", ".").replace("->", ".")
        return ""

    def h_call(self, node, ctx):
        name, receiver = self.split_callee(ctx, _field(node, "function"))
        if name:
            ctx.add_ref(node, "call", name, receiver)
        return 0

class CppHandler(CHandler):
    """扩展 C 规则，提取 C++ 命名空间、类、继承和类外方法定义。"""
    lang = "cpp"
    self_names = frozenset({"this"})
    builtin_funcs = CPP_BUILTINS
    bare_call_hits_class = True

    def register(self):
        """注册 C++ 节点回调并复用 C 回调；无返回值。"""
        super().register()
        self.bind({
            "class_specifier": self.h_record,
            "namespace_definition": self.h_namespace,
            "new_expression": self.h_new,
            "template_declaration": self.h_passthrough,
            "using_declaration": self.h_using,
            "field_initializer": self.h_passthrough,
        })

    def h_namespace(self, node, ctx):
        name_node = _field(node, "name")
        name = ctx.text(name_node) or "anonymous"
        definition = ctx.add_def(node, name, "namespace", name_node=name_node or node)
        if definition is None:
            return 0
        ctx.push(definition.fqn, "namespace", name, definition)
        return 0

    def h_passthrough(self, node, ctx):
        return 0

    def h_using(self, node, ctx):
        target = _first_of(node, {"qualified_identifier", "identifier", "type_identifier"})
        literal = ctx.text(target)
        parts = _split_qualified(literal)
        if len(parts) >= 2:
            ctx.add_import("::".join(parts[:-1]), alias=parts[-1], symbol=parts[-1], kind="symbol",
                           line=node.start_point[0] + 1)
        return SKIP_CHILDREN

    def h_new(self, node, ctx):
        type_node = _field(node, "type")
        if type_node is not None:
            parts = _split_qualified(ctx.text(type_node))
            if parts:
                ctx.add_ref(node, "new", parts[-1], "::".join(parts[:-1]))
        return 0
