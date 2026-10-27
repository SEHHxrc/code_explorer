"""旧路径兼容；语法到安全 IR 的适配实现已迁至 language_adapters。"""

from ..language_adapters.treesitter import TreeSitterIRCollector, TreeSitterSecurityFrontend

__all__ = ["TreeSitterIRCollector", "TreeSitterSecurityFrontend"]
