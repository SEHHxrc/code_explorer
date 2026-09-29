"""程序图语言前端共享的位置与身份工具。"""

from __future__ import annotations

import hashlib
from typing import Any

from backend.app.services.program_index import ProgramIdentity

from ..contracts import ProgramGraphLocation


def location_from_tree_sitter(path: str, node: Any) -> ProgramGraphLocation:
    """把 Tree-sitter 的零基坐标转换为公共一基源码范围。"""
    line = int(node.start_point[0]) + 1
    column = int(node.start_point[1]) + 1
    end_line = int(node.end_point[0]) + 1
    end_column = int(node.end_point[1]) + 1
    return make_location(path, line, column, end_line, end_column)


def make_location(
    path: str,
    line: int,
    column: int,
    end_line: int,
    end_column: int,
) -> ProgramGraphLocation:
    """构造带稳定位置身份的公共源码范围。"""
    normalized = ProgramIdentity.file_id(path)
    return ProgramGraphLocation(
        path=normalized,
        line=max(1, line),
        column=max(1, column),
        end_line=max(1, end_line),
        end_column=max(1, end_column),
        location_id=ProgramIdentity.location_id(
            normalized, line, column, end_line, end_column,
        ),
    )


def statement_id(method_id: str, location: ProgramGraphLocation, kind: str) -> str:
    """根据方法、位置和公共语句种类生成稳定节点身份。"""
    material = "\x1f".join((method_id, location.location_id, kind))
    return "op:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
