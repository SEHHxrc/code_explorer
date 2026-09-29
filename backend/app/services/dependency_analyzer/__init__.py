"""多语言静态依赖分析功能包。

本模块保持旧版 ``backend.app.services.dependency_analyzer`` 的公共导入接口。
"""

from __future__ import annotations

from .analyzer import UnifiedCodeAnalyzer
from .context import FileContext
from .handlers import (
    BaseHandler,
    CHandler,
    CppHandler,
    GoHandler,
    JavaHandler,
    JavaScriptHandler,
    PythonHandler,
    RustHandler,
    TypeScriptHandler,
    get_handler,
)
from .models import Definition, Frame, ImportRec, Reference

__all__ = [
    "BaseHandler",
    "CHandler",
    "CppHandler",
    "Definition",
    "FileContext",
    "Frame",
    "GoHandler",
    "ImportRec",
    "JavaHandler",
    "JavaScriptHandler",
    "PythonHandler",
    "Reference",
    "RustHandler",
    "TypeScriptHandler",
    "UnifiedCodeAnalyzer",
    "get_handler",
]
