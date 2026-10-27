"""兼容旧导入路径；实现唯一归属 language_backends，新代码使用其公共入口。"""

from ..language_backends.go import GoSemanticResolver, TOOL_DIRECTORY, WORKER_PATH

__all__ = ["GoSemanticResolver", "TOOL_DIRECTORY", "WORKER_PATH"]
