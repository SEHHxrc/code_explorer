"""历史语言前端入口；实现位于 language_adapters，不涉及网页前端。"""

from ..language_adapters import (
    CSecurityFrontend, CppSecurityFrontend, GoSecurityFrontend, JavaSecurityFrontend,
    JavaScriptSecurityFrontend, TypeScriptSecurityFrontend, PythonSecurityFrontend,
    LanguageFrontend, TreeSitterSecurityFrontend,
)

__all__ = ["CSecurityFrontend", "CppSecurityFrontend", "GoSecurityFrontend", "JavaSecurityFrontend",
           "JavaScriptSecurityFrontend", "TypeScriptSecurityFrontend", "PythonSecurityFrontend",
           "LanguageFrontend", "TreeSitterSecurityFrontend"]
