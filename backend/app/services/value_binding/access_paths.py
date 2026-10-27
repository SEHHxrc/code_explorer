"""跨语言词法访问路径公共协议；不隶属于某张图或某个语言前端。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from backend.app.services.syntax_analysis import (
    StaticIndex,
    normalize_static_index as normalize_static_index,
    parse_static_index_literal as parse_static_index_literal,
)

AccessPathSegmentKind: TypeAlias = Literal["attribute", "index", "selector", "unknown"]


@dataclass(frozen=True)
class AccessPathSegment:
    """一个字段或静态下标路径段。"""

    kind: AccessPathSegmentKind
    value: str | int

    def render(self) -> str:
        """将路径段序列化为稳定、跨语言可比较的文本。"""
        if self.kind == "attribute":
            return f".{self.value}"
        if self.kind == "selector":
            return f"[?{self.value}]"
        if self.kind == "unknown":
            return "[?]"
        if isinstance(self.value, int):
            return f"[{self.value}]"
        encoded = json.dumps(self.value, ensure_ascii=False, separators=(",", ":"))
        return f"[{encoded}]"


@dataclass(frozen=True)
class AccessPath:
    """以根变量为起点的结构化词法访问路径。"""

    root: str
    segments: tuple[AccessPathSegment, ...] = ()

    def attribute(self, name: str) -> AccessPath:
        """返回追加字段段的新路径。"""
        return AccessPath(
            root=self.root,
            segments=(*self.segments, AccessPathSegment("attribute", name)),
        )

    def index(self, value: StaticIndex) -> AccessPath:
        """返回追加静态下标段的新路径。"""
        return AccessPath(
            root=self.root,
            segments=(*self.segments, AccessPathSegment("index", value)),
        )

    def render(self) -> str:
        """生成供 Def/Use 与值流边使用的规范化变量身份。"""
        return self.root + "".join(segment.render() for segment in self.segments)


@runtime_checkable
class AccessPathResolver(Protocol):
    """语言前端把本语言 AST 节点解析为公共访问路径的结构协议。"""

    def resolve_access_path(self, node: Any | None) -> AccessPath | None:
        """返回可确定的词法路径；动态或不支持的表达式返回 ``None``。"""
        ...


def render_access_path(resolver: AccessPathResolver, node: Any | None) -> str:
    """通过语言解析器生成访问路径文本，无法精确解析时返回空串。"""
    path = resolver.resolve_access_path(node)
    return path.render() if path is not None else ""


def attribute_path(name: str) -> AccessPath | None:
    """读取前端已规范化的纯字段路径；拒绝调用、下标和任意表达式，不替代 AST 解析。"""
    parts = name.split(".")
    if not all(part.isidentifier() for part in parts):
        return None
    return AccessPath(parts[0], tuple(AccessPathSegment("attribute", part) for part in parts[1:]))


def canonical_path(name: str, *, selectors: bool = False) -> AccessPath | None:
    """读取已规范化的静态字段/元素路径；不执行源码，不接受动态下标或任意表达式。"""
    end = next((position for position, char in enumerate(name) if char in ".["), len(name))
    root, position, segments = name[:end], end, []
    if not (root.isidentifier() or root.startswith("call-result:") and len(root) > 12):
        return None
    decoder = json.JSONDecoder()
    while position < len(name) and len(segments) < 32:
        if name[position] == ".":
            start = position + 1
            position = next((index for index in range(start, len(name)) if name[index] in ".["), len(name))
            value = name[start:position]
            if not (value.isidentifier() or value == "*"):
                return None
            segments.append(AccessPathSegment("attribute", value))
        elif name[position] == "[":
            if selectors and name.startswith("[?", position):
                end = name.find("]", position + 2)
                if end < 0:
                    return None
                key = name[position + 2:end]
                if key and not key.isidentifier():
                    return None
                segments.append(AccessPathSegment("selector" if key else "unknown", key))
                position = end + 1
                continue
            try:
                value, end = decoder.raw_decode(name, position + 1)
            except (ValueError, TypeError):
                return None
            value = normalize_static_index(value)
            if value is None or end >= len(name) or name[end] != "]":
                return None
            segments.append(AccessPathSegment("index", value))
            position = end + 1
        else:
            return None
    return AccessPath(root, tuple(segments)) if position == len(name) else None


def member_path(root: str, member: str) -> str:
    """连接一个直属字段或规范元素后缀；键中包含点号时也不拆分。"""
    return root + ("" if member.startswith("[") else ".") + member


def parent_path(name: str) -> str:
    """返回规范访问路径的父路径；无成员或非法路径返回空串。"""
    path = canonical_path(name, selectors=True)
    return AccessPath(path.root, path.segments[:-1]).render() if path and path.segments else ""


def descendant(name: str, root: str) -> bool:
    """是否为根路径的成员，避免 a/b 与 ab 以及字符串键内点号的错误匹配。"""
    return name.startswith(root + ".") or name.startswith(root + "[")


__all__ = [
    "AccessPath",
    "AccessPathResolver",
    "AccessPathSegment",
    "AccessPathSegmentKind",
    "StaticIndex",
    "normalize_static_index",
    "parse_static_index_literal",
    "render_access_path",
    "attribute_path",
    "canonical_path",
    "member_path",
    "parent_path",
    "descendant",
]
