# -*- coding: utf-8 -*-
from __future__ import annotations

"""单文件解析上下文和作用域状态。"""
import os

from .ast_utils import _build_symbol, _norm_type, _text
from .constants import SYMBOL_KIND
from .models import Definition, Frame, ImportRec, Reference

class FileContext:
    """单文件解析上下文：阶段一的所有产出都挂在这里，线程内独占。"""

    __slots__ = ("path", "abs_path", "lang", "src", "defs", "refs", "imports",
                 "symbols", "symbol_index", "def_index", "frames", "package",
                 "handler", "var_type_table", "pending_typedef")

    def __init__(self, path: str, abs_path: str, lang: str, src: bytes, handler):
        self.path = path
        self.abs_path = abs_path
        self.lang = lang
        self.src = src
        self.handler = handler
        self.defs: list[Definition] = []
        self.refs: list[Reference] = []
        self.imports: list[ImportRec] = []
        self.symbols: list[dict] = []
        self.symbol_index: set = set()
        self.def_index: dict = {}
        self.package: str = ""
        self.pending_typedef: str = ""
        # (文件, 所属函数 fqn) -> {变量名: 类型字面量}，阶段三解析 obj.method() 时查表
        self.var_type_table: dict = {}
        self.frames: list[Frame] = [Frame(fqn=path, kind="module", name=os.path.basename(path))]

    # -- 作用域 ---------------------------------------------------------
    @property
    def top(self) -> Frame:
        """输出当前最内层作用域帧。"""
        return self.frames[-1]

    def push(self, fqn: str, kind: str, name: str = "", definition=None, owner_literal: str = "") -> Frame:
        """输入作用域信息并压栈，输出新建帧。"""
        frame = Frame(fqn=fqn, kind=kind, name=name, definition=definition, owner_literal=owner_literal)
        self.frames.append(frame)
        return frame

    def pop(self):
        """弹出非模块作用域；无返回值。"""
        if len(self.frames) > 1:
            self.frames.pop()

    def enclosing_class(self) -> str:
        """输出最近类或 impl 作用域的限定名，不存在时输出空串。"""
        for frame in reversed(self.frames):
            if frame.kind in ("class", "impl"):
                return frame.fqn
        return ""

    def enclosing_class_frame(self) -> Frame | None:
        """输出最近类或 impl 帧，不存在时输出 ``None``。"""
        for frame in reversed(self.frames):
            if frame.kind in ("class", "impl"):
                return frame
        return None

    def enclosing_callable(self) -> str:
        """输出最近函数/方法限定名，模块级引用则输出当前作用域。"""
        for frame in reversed(self.frames):
            if frame.kind in ("function", "method"):
                return frame.fqn
        return self.frames[-1].fqn

    def in_callable(self) -> bool:
        """输出当前遍历位置是否位于函数或方法内。"""
        return any(f.kind in ("function", "method") for f in self.frames)

    def scope_names(self) -> list[str]:
        """输出除模块外的嵌套作用域名称列表。"""
        return [f.name for f in self.frames[1:] if f.name]

    # -- 变量类型 -------------------------------------------------------
    def set_var_type(self, name: str, type_literal: str):
        """记录当前可调用作用域内变量类型；输入变量名和类型字面量。"""
        type_literal = _norm_type(type_literal)
        if not name or not type_literal:
            return
        target = self.frames[0]
        for frame in reversed(self.frames):
            if frame.kind in ("function", "method"):
                target = frame
                break
        target.var_types[name] = type_literal
        self.var_type_table.setdefault(target.fqn, {})[name] = type_literal

    def bind_frame_var(self, frame: Frame, name: str, type_literal: str):
        """把 self / this / Go 接收者等绑定到指定帧（同时写入跨阶段查表）。"""
        type_literal = _norm_type(type_literal)
        if not name or not type_literal:
            return
        frame.var_types[name] = type_literal
        self.var_type_table.setdefault(frame.fqn, {})[name] = type_literal

    def get_var_type(self, name: str) -> str:
        """从内向外查找变量类型；未找到时输出空串。"""
        for frame in reversed(self.frames):
            hit = frame.var_types.get(name)
            if hit:
                return hit
        return ""

    # -- 产出 -----------------------------------------------------------
    def add_def(self, node, name: str, kind: str, *, name_node=None, owner_literal: str = "",
                bases=None, implements=None, type_literal: str = "", is_declaration: bool = False,
                parent_override: str = "", return_type: str = "") -> Definition | None:
        """输入语法节点及定义元数据，去重后记录并输出 ``Definition``。"""
        if not name:
            return None
        parent = parent_override or self.top.fqn
        fqn = f"{parent}::{name}"
        existing = self.def_index.get(fqn)
        if existing is not None:
            # 同一符号的多次出现（如 C 原型 + 定义、Python 多处 self.x 赋值）合并
            if bases:
                for base in bases:
                    if base not in existing.bases:
                        existing.bases.append(base)
            if implements:
                for itf in implements:
                    if itf not in existing.implements:
                        existing.implements.append(itf)
            if type_literal and not existing.type_literal:
                existing.type_literal = type_literal
            if return_type and not existing.return_type:
                existing.return_type = _norm_type(return_type)
            if existing.is_declaration and not is_declaration:
                existing.is_declaration = False
                existing.line = node.start_point[0] + 1
                existing.end_line = node.end_point[0] + 1
            return existing

        definition = Definition(
            fqn=fqn, name=name, kind=kind, file=self.path, lang=self.lang,
            line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            parent_fqn=parent, owner_literal=owner_literal,
            bases=list(bases or []), implements=list(implements or []),
            type_literal=_norm_type(type_literal), return_type=_norm_type(return_type),
            is_declaration=is_declaration,
        )
        self.defs.append(definition)
        self.def_index[fqn] = definition

        symbol_kind = SYMBOL_KIND.get(kind)
        if symbol_kind:
            sym_scope = fqn[len(self.path) + 2:] if fqn.startswith(self.path + "::") else name
            sym_fqn = sym_scope.replace("::", ".")
            key = (sym_fqn, symbol_kind)
            if key not in self.symbol_index:
                self.symbol_index.add(key)
                self.symbols.append(_build_symbol(name_node or node, name, symbol_kind, sym_fqn))
        return definition

    def add_ref(self, node, kind: str, name: str, receiver: str = ""):
        """输入语法节点和目标信息，追加一条待解析引用；无返回值。"""
        if not name:
            return
        self.refs.append(Reference(
            file=self.path, from_fqn=self.enclosing_callable(), kind=kind, name=name,
            receiver=receiver, line=node.start_point[0] + 1,
            class_fqn=self.enclosing_class(), lang=self.lang,
        ))

    def add_import(self, module: str, *, alias: str = "", symbol: str = "", kind: str = "module", line: int = 0):
        """输入模块、别名和位置，追加一条导入记录；无返回值。"""
        if not module:
            return
        self.imports.append(ImportRec(file=self.path, module=module, alias=alias or symbol or module.split("/")[-1],
                                      symbol=symbol, kind=kind, line=line))

    def text(self, node) -> str:
        """输入 Tree-sitter 节点，输出其 UTF-8 源码文本。"""
        return _text(node, self.src)
