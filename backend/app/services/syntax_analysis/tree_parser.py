"""跨分析模块共享的 Tree-sitter 读取、归一化和解析器池。"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Set

import tree_sitter
from tree_sitter_language_pack import get_parser


class TreeSitterParserPool:
    """在线程本地缓存语言解析器，避免跨线程共享可变 Parser。"""

    def __init__(self) -> None:
        """初始化线程本地解析器容器。"""
        self._local = threading.local()

    def get(self, language: str) -> tree_sitter.Parser:
        """返回当前线程指定语言的解析器，不存在时按需创建。"""
        parsers = getattr(self._local, "parsers", None)
        if parsers is None:
            parsers = {}
            self._local.parsers = parsers
        parser = parsers.get(language)
        if parser is None:
            parser = get_parser(language)
            parsers[language] = parser
        return parser


DEFAULT_TREE_SITTER_PARSER_POOL = TreeSitterParserPool()
"""依赖图和 ProgramGraph 默认共享的线程本地解析器池。"""


def text(node: tree_sitter.Node | None, source: bytes) -> str:
    """容错读取节点对应的 UTF-8 源码。"""
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def field(node: tree_sitter.Node | None, name: str) -> tree_sitter.Node | None:
    """安全读取 Tree-sitter 节点的单个命名字段。"""
    if node is None:
        return None
    try:
        return node.child_by_field_name(name)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def fields(node: tree_sitter.Node | None, name: str) -> list[tree_sitter.Node]:
    """安全读取 Tree-sitter 节点的同名字段列表。"""
    if node is None:
        return []
    try:
        return list(node.children_by_field_name(name))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []


def first_of(
    node: tree_sitter.Node | None,
    node_types: Set[str],
) -> tree_sitter.Node | None:
    """返回第一个命中类型的直接具名子节点。"""
    if node is None:
        return None
    return next(
        (child for child in node.named_children if child.type in node_types),
        None,
    )


def descend_for(
    node: tree_sitter.Node | None,
    node_types: Set[str],
    max_depth: int = 6,
) -> tree_sitter.Node | None:
    """在有限深度内广度优先查找指定类型节点。"""
    if node is None:
        return None
    pending = deque([(node, 0)])
    while pending:
        current, depth = pending.popleft()
        if current.type in node_types:
            return current
        if depth < max_depth:
            pending.extend((child, depth + 1) for child in current.named_children)
    return None


def normalize_type(literal: str) -> str:
    """把指针、泛型和限定符类型文本归一化为主要类型名称。"""
    if not literal:
        return ""
    normalized = literal.strip()
    qualifiers = (
        "const ", "volatile ", "static ", "mut ", "final ", "unsafe ",
        "struct ", "enum ", "union ", "class ",
    )
    for qualifier in qualifiers:
        while normalized.startswith(qualifier):
            normalized = normalized[len(qualifier):].strip()
    normalized = normalized.lstrip("*&")
    if "<" in normalized:
        normalized = normalized.split("<", 1)[0]
    if "[" in normalized:
        normalized = normalized.split("[", 1)[0]
    normalized = (
        normalized.replace("(", "")
        .replace(")", "")
        .replace("*", "")
        .replace("&", "")
    )
    return normalized.rstrip("?!").strip()


def split_qualified(literal: str) -> list[str]:
    """把点号、双冒号或箭头限定名称拆成非空片段。"""
    normalized = literal.replace("::", ".").replace("->", ".")
    return [part for part in normalized.split(".") if part]
