"""语言特有的参数绑定和未来数据传播语义。"""

from .base import ArgumentBinding, LanguageSemantics, PositionalLanguageSemantics
from .c_family import CLanguageSemantics, CppLanguageSemantics
from .go import GoLanguageSemantics
from .java import JavaLanguageSemantics
from .javascript import JavaScriptLanguageSemantics, TypeScriptLanguageSemantics
from .python import PythonLanguageSemantics

__all__ = [
    "ArgumentBinding",
    "CLanguageSemantics",
    "CppLanguageSemantics",
    "GoLanguageSemantics",
    "JavaLanguageSemantics",
    "JavaScriptLanguageSemantics",
    "TypeScriptLanguageSemantics",
    "LanguageSemantics",
    "PythonLanguageSemantics",
    "PositionalLanguageSemantics",
]
