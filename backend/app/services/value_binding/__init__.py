"""语言无关的结构化值绑定协议；不拥有 AST、CFG、调用解析或安全规则。"""

from .contracts import (
    BindingPattern,
    BindingProjection,
    BindingResolution,
    BindingValue,
    PatternMember,
    ValueMember,
    PatternResolver,
)
from .resolver import StructuredBindingResolver, append_selector

__all__ = [
    "BindingPattern",
    "BindingProjection",
    "BindingResolution",
    "BindingValue",
    "PatternMember",
    "ValueMember",
    "PatternResolver",
    "StructuredBindingResolver",
    "append_selector",
]
