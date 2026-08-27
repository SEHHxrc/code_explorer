# -*- coding: utf-8 -*-
from __future__ import annotations

"""语言节点处理器的公共分派接口。"""
from ..ast_utils import _field, _first_of
from ..context import FileContext

class BaseHandler:
    """语言处理器基类：只声明分发表与语言常量，具体节点处理由子类实现。"""

    lang = ""
    self_names = frozenset({"self", "this"})
    super_names = frozenset({"super"})
    builtin_funcs = frozenset()
    builtin_objects = frozenset()
    type_methods = frozenset()      # 内置类型的实例方法（接收者类型未知时的兜底判定）
    stdlib_modules = frozenset()
    # 无接收者的裸调用是否可能命中当前类的方法（Python/Java/C++/JS 可以，Go/Rust 不行）
    bare_call_hits_class = True

    def __init__(self):
        self.handlers = {}
        self.register()

    def register(self):
        """由子类绑定节点类型与回调；无输入和返回值。"""
        raise NotImplementedError

    def bind(self, mapping: dict):
        """输入节点类型到回调的映射并合并到分发表。"""
        self.handlers.update(mapping)

    # -- 供各语言复用的调用目标拆解 --------------------------------------
    def split_callee(self, ctx: FileContext, func_node) -> tuple[str, str]:
        """返回 (被调用名, 接收者原文)。默认实现覆盖绝大多数语言的成员访问节点。"""
        if func_node is None:
            return "", ""
        node_type = func_node.type
        if node_type in ("identifier", "field_identifier", "type_identifier",
                         "property_identifier", "name", "package_identifier"):
            return ctx.text(func_node), ""
        if node_type in ("attribute", "member_expression", "field_expression",
                         "selector_expression", "scoped_identifier", "qualified_identifier"):
            attr = (_field(func_node, "attribute") or _field(func_node, "property")
                    or _field(func_node, "field") or _field(func_node, "name"))
            obj = (_field(func_node, "object") or _field(func_node, "argument")
                   or _field(func_node, "operand") or _field(func_node, "value")
                   or _field(func_node, "path") or _field(func_node, "scope"))
            if attr is not None:
                return ctx.text(attr), ctx.text(obj).strip()
            return "", ""
        if node_type == "parenthesized_expression":
            return self.split_callee(ctx, _first_of(func_node, {"identifier", "attribute", "member_expression",
                                                               "field_expression", "selector_expression"}))
        return "", ""

    def is_builtin_call(self, name: str, receiver: str) -> bool:
        """输入调用名和接收者，输出是否属于该语言内置调用。"""
        if receiver:
            return receiver.split(".")[0] in self.builtin_objects
        return name in self.builtin_funcs

    def is_stdlib_module(self, module: str) -> bool:
        """输入模块字面量，输出其根模块是否属于语言标准库。"""
        normalized = module.removeprefix("node:")
        root = normalized.replace("::", "/").split("/")[0].split(".")[0]
        return root in self.stdlib_modules
