"""安全分析语言前端。"""

from .base import LanguageFrontend
from .go import GoSecurityFrontend
from .java import JavaSecurityFrontend
from .python import PythonSecurityFrontend
from .treesitter import TreeSitterSecurityFrontend

__all__ = [
    "JavaSecurityFrontend",
    "GoSecurityFrontend",
    "LanguageFrontend",
    "PythonSecurityFrontend",
    "TreeSitterSecurityFrontend",
]
