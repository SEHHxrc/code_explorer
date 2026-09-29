"""语言特有的参数绑定和未来数据传播语义。"""

from .base import ArgumentBinding, LanguageSemantics
from .go import GoLanguageSemantics
from .java import JavaLanguageSemantics
from .python import PythonLanguageSemantics

__all__ = [
    "ArgumentBinding",
    "GoLanguageSemantics",
    "JavaLanguageSemantics",
    "LanguageSemantics",
    "PythonLanguageSemantics",
]
