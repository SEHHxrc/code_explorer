"""依赖分析兼容工具；通用 Tree-sitter 能力由 syntax_analysis 提供。"""

from __future__ import annotations

import tree_sitter

from backend.app.services.syntax_analysis import (
    descend_for as _descend_for,
)
from backend.app.services.syntax_analysis import (
    field as _field,
)
from backend.app.services.syntax_analysis import (
    fields as _fields,
)
from backend.app.services.syntax_analysis import (
    first_of as _first_of,
)
from backend.app.services.syntax_analysis import (
    normalize_type as _norm_type,
)
from backend.app.services.syntax_analysis import (
    split_qualified as _split_qualified,
)
from backend.app.services.syntax_analysis import (
    text as _text,
)

from .constants import NOISE_NAMES

__all__ = [
    "_build_symbol",
    "_descend_for",
    "_field",
    "_fields",
    "_first_of",
    "_is_meaningful_name",
    "_norm_type",
    "_split_qualified",
    "_text",
    "tree_sitter",
]

def _build_symbol(node: tree_sitter.Node, name: str, kind: str, fqn: str) -> dict:
    """构造前端文件大纲使用的符号（结构与旧版完全一致）。"""
    start_point = node.start_point
    end_point = node.end_point
    return {
        "name": name,
        "kind": kind,
        "fully_qualified_name": fqn,
        "extent_utf16": {
            "start": {"line_number": start_point[0] + 1, "utf16_col": start_point[1]},
            "end": {"line_number": end_point[0] + 1, "utf16_col": end_point[1]},
        },
    }

def _is_meaningful_name(name: str) -> bool:
    """过滤全局/字段层面的噪音名（单字母临时变量、私有 dunder）。"""
    return bool(name) and name.lower() not in NOISE_NAMES and not name.startswith("__")
