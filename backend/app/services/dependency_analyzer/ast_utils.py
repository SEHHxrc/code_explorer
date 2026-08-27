# -*- coding: utf-8 -*-
from __future__ import annotations

"""Tree-sitter 节点读取、符号构造和类型文本归一化工具。"""
from collections import deque

import tree_sitter

from .constants import NOISE_NAMES

def _text(node: tree_sitter.Node | None, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

def _field(node: tree_sitter.Node | None, name: str) -> tree_sitter.Node | None:
    if node is None:
        return None
    try:
        return node.child_by_field_name(name)
    except Exception:
        return None

def _fields(node: tree_sitter.Node | None, name: str) -> list:
    if node is None:
        return []
    try:
        return list(node.children_by_field_name(name))
    except Exception:
        return []

def _first_of(node: tree_sitter.Node | None, types) -> tree_sitter.Node | None:
    """在直接子节点中找第一个指定类型的节点（不递归）。"""
    if node is None:
        return None
    for child in node.named_children:
        if child.type in types:
            return child
    return None

def _descend_for(node: tree_sitter.Node | None, types, max_depth: int = 6):
    """在有限深度内向下找第一个指定类型的节点（用于拆解声明符等小子树）。"""
    if node is None:
        return None
    queue = deque([(node, 0)])
    while queue:
        cur, depth = queue.popleft()
        if cur.type in types:
            return cur
        if depth >= max_depth:
            continue
        for child in cur.named_children:
            queue.append((child, depth + 1))
    return None

def _build_symbol(node: tree_sitter.Node, name: str, kind: str, fqn: str) -> dict:
    """构造前端文件大纲使用的符号（结构与旧版完全一致）。"""
    start_point = node.start_point
    end_point = node.end_point
    return {
        "name": name,
        "kind": kind,
        "fully_qualified_name": fqn,
        "extent_utf16": {
            "start": {"line_number": start_point[0] + 1, "utf16_col": start_point[1]},
            "end": {"line_number": end_point[0] + 1, "utf16_col": end_point[1]},
        },
    }

def _norm_type(literal: str) -> str:
    """把类型字面量归一化成可查找的名字：``*Animal`` / ``List<Animal>`` / ``const Foo&`` -> 主类型名。"""
    if not literal:
        return ""
    lit = literal.strip()
    for kw in ("const ", "volatile ", "static ", "mut ", "final ", "unsafe ", "struct ", "enum ", "union ", "class "):
        while lit.startswith(kw):
            lit = lit[len(kw):].strip()
    lit = lit.lstrip("*&")
    if "<" in lit:
        lit = lit.split("<", 1)[0]
    if "[" in lit:
        lit = lit.split("[", 1)[0]
    lit = lit.replace("(", "").replace(")", "").replace("*", "").replace("&", "")
    lit = lit.rstrip("?!").strip()
    return lit

def _split_qualified(literal: str) -> list[str]:
    """把 ``a.b.c`` / ``a::b::c`` / ``a->b`` 拆成片段。"""
    if not literal:
        return []
    tmp = literal.replace("::", ".").replace("->", ".")
    return [p for p in tmp.split(".") if p]

def _is_meaningful_name(name: str) -> bool:
    """过滤全局/字段层面的噪音名（单字母临时变量、私有 dunder）。"""
    return bool(name) and name.lower() not in NOISE_NAMES and not name.startswith("__")

__all__ = [name for name in globals() if not name.startswith('__')]
