"""C/C++ 共享的纯语法声明符读取，不执行符号绑定或安全判断。"""

from __future__ import annotations

from typing import Any

from .tree_parser import field


def unwrap_c_declarator(node: Any | None) -> tuple[Any | None, Any | None]:
    """输入声明符，返回名称节点和参数容器；最多展开十六层指针/引用包装。"""
    current = node
    parameters = None
    wrappers = {
        "pointer_declarator", "array_declarator", "parenthesized_declarator",
        "reference_declarator", "abstract_pointer_declarator", "attributed_declarator",
    }
    for _ in range(16):
        if current is None:
            break
        if current.type in wrappers:
            current = field(current, "declarator")
        elif current.type == "function_declarator":
            parameters = field(current, "parameters")
            current = field(current, "declarator")
        else:
            break
    return current, parameters
