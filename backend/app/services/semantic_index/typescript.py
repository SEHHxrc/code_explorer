"""TypeScript 后端旧路径兼容入口；实现唯一归属 language_backends。"""

from ..language_backends.typescript import TOOL_DIRECTORY, TypeScriptSemanticResolver

__all__ = ["TOOL_DIRECTORY", "TypeScriptSemanticResolver"]
