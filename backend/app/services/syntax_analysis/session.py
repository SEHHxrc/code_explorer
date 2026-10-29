"""单任务、有界语法复用；只缓存真实字节对应的解析，不缓存文件权限或安全结论。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import threading
from typing import Any, ParamSpec, TypeVar

import tree_sitter

from .tree_parser import DEFAULT_TREE_SITTER_PARSER_POOL, TreeSitterParserPool

P = ParamSpec("P")
T = TypeVar("T")
_CURRENT: ContextVar[SyntaxSession | None] = ContextVar("analysis_syntax_session", default=None)


class SyntaxSession:
    """任务内固定保留有界批次，避免整仓顺序重扫冲刷；Tree 通过 copy 隔离句柄。"""

    def __init__(self, *, max_entries: int = 4096, max_estimated_bytes: int = 64 * 1024 * 1024) -> None:
        """设置总条目/估算存储额度；估算包含源码和节点开销，不是进程 RSS 硬限制。"""
        if max_entries < 1 or max_estimated_bytes < 1:
            raise ValueError("Syntax session limits must be positive")
        self.max_entries = max_entries
        self.max_estimated_bytes = max_estimated_bytes
        self._items: dict[tuple[object, ...], tuple[Any, int]] = {}
        self._bytes = 0
        self._lock = threading.RLock()
        self._counts: Counter[str] = Counter()
        self._closed = False

    def memo(self, key: tuple[object, ...], build: Callable[[], T], size: Callable[[T], int],
             clone: Callable[[T], T] = lambda value: value) -> T:
        """按内容键复用；失败不缓存，超额直接交付，所有消费者仍执行原有文件安全检查。"""
        domain = str(key[0])
        with self._lock:
            if not self._closed and key in self._items:
                value, _weight = self._items[key]
                self._counts[domain + "_hits"] += 1
                return clone(value)
            self._counts[domain + "_misses"] += 1
        value = build()
        weight = size(value)
        with self._lock:
            if not self._closed and key not in self._items:
                if weight > self.max_estimated_bytes:
                    self._counts["oversized_not_retained"] += 1
                elif len(self._items) >= self.max_entries or self._bytes + weight > self.max_estimated_bytes:
                    self._counts["capacity_not_retained"] += 1
                else:
                    self._items[key] = (clone(value), weight)
                    self._bytes += weight
        return value

    def snapshot(self) -> dict[str, int]:
        """只返回计数和估算存储，不泄漏文件路径、源码或身份哈希。"""
        with self._lock:
            return {**self._counts, "entries": len(self._items), "estimated_bytes": self._bytes,
                    "max_entries": self.max_entries, "max_estimated_bytes": self.max_estimated_bytes}

    def close(self) -> None:
        """任务结束或失败时释放树及源码引用；晚到的任务不能重新填充已关闭会话。"""
        with self._lock:
            self._closed = True
            self._items.clear()
            self._bytes = 0


@contextmanager
def syntax_session(*, max_entries: int = 4096, max_estimated_bytes: int = 64 * 1024 * 1024,
                   reuse: bool = False) -> Iterator[SyntaxSession]:
    """缺省隔离新任务；嵌套生产入口可显式复用，退出恢复调用方上下文。"""
    current = _CURRENT.get()
    if reuse and current is not None:
        yield current
        return
    session = SyntaxSession(max_entries=max_entries, max_estimated_bytes=max_estimated_bytes)
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        _CURRENT.reset(token)
        session.close()


def reuse_syntax(function: Callable[P, T]) -> Callable[P, T]:
    """为同步分析入口建立/复用会话，不改变参数、返回值、异常或未缓存时的分析过程。"""
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        """在最外层调用退出时清理任务资源，线程池通过既有 copy_context 继承。"""
        with syntax_session(reuse=True):
            return function(*args, **kwargs)
    return wrapped


def syntax_statistics() -> dict[str, int]:
    """读取当前任务的可选观测；无会话时不创建缓存。"""
    current = _CURRENT.get()
    return current.snapshot() if current is not None else {}


def parse_tree(path: str, source: bytes, grammar: str,
               pool: TreeSitterParserPool = DEFAULT_TREE_SITTER_PARSER_POOL) -> tree_sitter.Tree:
    """以真实内容、语法及池身份查询树；返回独立句柄，调用方不得修改公共缓存。"""
    current = _CURRENT.get()
    if current is None:
        return pool.get(grammar).parse(source)
    key = ("tree", path.replace("\\", "/"), grammar, pool, hashlib.sha256(source).digest())
    return current.memo(key, lambda: pool.get(grammar).parse(source),
                        lambda tree: len(source) + tree.root_node.descendant_count * 128,
                        lambda tree: tree.copy())
