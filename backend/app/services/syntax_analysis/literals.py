"""跨语言静态字面量的纯语法解析工具。"""

from __future__ import annotations

import ast
import re
from typing import TypeAlias

StaticIndex: TypeAlias = str | int

_INTEGER_SUFFIX = re.compile(r"(?i)(?:[iu](?:8|16|32|64|128|size)|[ul]+)$")


def normalize_static_index(value: object) -> StaticIndex | None:
    """只接受不会依赖运行时状态的字符串或整数下标。"""
    if type(value) is int:
        return value
    if isinstance(value, str) and len(value) <= 256:
        return value
    return None


def parse_static_index_literal(literal: str) -> StaticIndex | None:
    """解析常见语言共享的字符串或整数常量下标。"""
    text = literal.strip()
    if not text or len(text) > 512:
        return None
    try:
        parsed = normalize_static_index(ast.literal_eval(text))
    except (SyntaxError, ValueError):
        parsed = None
    if parsed is not None:
        return parsed
    normalized = _INTEGER_SUFFIX.sub("", text.replace("_", "").replace("'", ""))
    sign = -1 if normalized.startswith("-") else 1
    unsigned = normalized.lstrip("+-")
    try:
        if unsigned.lower().startswith(("0x", "0b", "0o")):
            value = int(unsigned, 0)
        elif len(unsigned) > 1 and unsigned.startswith("0"):
            value = int(unsigned, 8)
        else:
            value = int(unsigned, 10)
    except ValueError:
        return None
    return normalize_static_index(sign * value)
