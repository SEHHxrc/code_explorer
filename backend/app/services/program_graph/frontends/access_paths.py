"""跨语言词法访问路径的公共模型与解析协议。"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

AccessPathSegmentKind: TypeAlias = Literal["attribute", "index"]
StaticIndex: TypeAlias = str | int


@dataclass(frozen=True)
class AccessPathSegment:
    """一个字段或静态下标路径段。"""

    kind: AccessPathSegmentKind
    value: str | int

    def render(self) -> str:
        """将路径段序列化为稳定、跨语言可比较的文本。"""
        if self.kind == "attribute":
            return f".{self.value}"
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


def normalize_static_index(value: object) -> StaticIndex | None:
    """只接受不会依赖运行时状态的字符串或整数下标。"""
    if type(value) is int:
        return value
    if isinstance(value, str) and len(value) <= 256:
        return value
    return None


_INTEGER_SUFFIX = re.compile(r"(?i)(?:[iu](?:8|16|32|64|128|size)|[ul]+)$")


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
