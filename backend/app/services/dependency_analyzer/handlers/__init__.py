# -*- coding: utf-8 -*-
from __future__ import annotations

"""语言处理器注册表与线程安全实例缓存。"""
import threading

from .base import BaseHandler
from .c_family import CHandler, CppHandler
from .go import GoHandler
from .java import JavaHandler
from .javascript import JavaScriptHandler, TypeScriptHandler
from .python import PythonHandler
from .rust import RustHandler

HANDLER_CLASSES = {
    "python": PythonHandler,
    "javascript": JavaScriptHandler,
    "typescript": TypeScriptHandler,
    "go": GoHandler,
    "java": JavaHandler,
    "c": CHandler,
    "cpp": CppHandler,
    "rust": RustHandler,
}

_HANDLER_CACHE = {}
_HANDLER_LOCK = threading.Lock()


def get_handler(lang: str) -> BaseHandler | None:
    """输入语言键，输出可跨线程共享的无状态处理器；不支持时输出 ``None``。"""
    handler = _HANDLER_CACHE.get(lang)
    if handler is None:
        cls = HANDLER_CLASSES.get(lang)
        if cls is None:
            return None
        with _HANDLER_LOCK:
            handler = _HANDLER_CACHE.get(lang)
            if handler is None:
                handler = cls()
                _HANDLER_CACHE[lang] = handler
    return handler

__all__ = [
    "BaseHandler", "PythonHandler", "JavaScriptHandler", "TypeScriptHandler",
    "GoHandler", "JavaHandler", "CHandler", "CppHandler", "RustHandler", "get_handler",
]
