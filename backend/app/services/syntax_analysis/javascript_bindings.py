"""JS/TS AST 到公共结构化绑定 IR 的适配，供程序图和安全前端共同使用。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field as dataclass_field
from functools import lru_cache, wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar

from backend.app.services.program_index import ProgramIdentity
from backend.app.services.value_binding import (
    BindingPattern,
    BindingValue,
    PatternMember,
    ValueMember,
    StructuredBindingResolver,
    BindingResolution,
)

from .javascript import javascript_access_name
from .literals import parse_static_index_literal
from .tree_parser import field, text


P = ParamSpec("P")
T = TypeVar("T")


@dataclass(slots=True)
class _BindingCache:
    """单文件缓存状态；functools 的动态缓存包装器保存在私有表中。"""

    source: bytes
    path: str
    max_entries: int
    functions: dict[Callable[..., Any], Any] = dataclass_field(default_factory=dict)


_BINDING_CACHE: ContextVar[_BindingCache | None] = ContextVar("javascript_binding_cache", default=None)


def _cached_binding(function: Callable[P, T]) -> Callable[P, T]:
    """统一绑定函数的缓存分发；保留签名、节点/深度键及源码对象隔离。"""
    source_position = tuple(signature(function).parameters).index("source")

    @wraps(function)
    def cached(*args: P.args, **kwargs: P.kwargs) -> T:
        """仅在匹配的文件作用域内使用有界 LRU，其他调用直接计算。"""
        cache = _BINDING_CACHE.get()
        source = args[source_position] if len(args) > source_position else kwargs.get("source")
        path = args[source_position + 1] if len(args) > source_position + 1 else kwargs.get("path")
        if cache is None or source is not cache.source or path != cache.path:
            return function(*args, **kwargs)
        memoized = cache.functions.get(cached)
        if memoized is None:
            memoized = cache.functions[cached] = lru_cache(maxsize=cache.max_entries)(function)
        return memoized(*args, **kwargs)

    return cached


@contextmanager
def javascript_binding_scope(source: bytes, path: str, *, max_entries: int = 1024) -> Iterator[_BindingCache]:
    """在单文件收集期间复用纯结果，支持线程/异步隔离、嵌套恢复和异常清理。"""
    if max_entries < 1:
        raise ValueError("Binding cache size must be positive")
    cache = _BindingCache(source, path, max_entries)
    token = _BINDING_CACHE.set(cache)
    try:
        yield cache
    finally:
        _BINDING_CACHE.reset(token)
        for function in cache.functions.values():
            function.cache_clear()


def _origin(node: Any, path: str) -> str:
    """让叶值溯源可与安全事实的精确源码身份匹配。"""
    return ProgramIdentity.location_id(
        path,
        node.start_point[0] + 1,
        node.start_point[1] + 1,
        node.end_point[0] + 1,
        node.end_point[1] + 1,
    )


@_cached_binding
def javascript_pattern(
    node: Any, source: bytes, path: str, depth: int = 0
) -> BindingPattern:
    """识别嵌套对象/数组、别名、默认值、rest 和静态计算键；动态键显式未知。"""
    if node is None or depth > 24:
        return BindingPattern("unknown", reason="binding_pattern_depth_exceeded")
    raw = text(node, source)
    if node.type in {
        "identifier",
        "shorthand_property_identifier_pattern",
        "member_expression",
        "subscript_expression",
    }:
        return BindingPattern("name", name=javascript_access_name(node, source) or raw)
    if node.type in {"required_parameter", "optional_parameter"}:
        return javascript_pattern(
            field(node, "pattern") or field(node, "name"), source, path, depth + 1
        )
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        target = field(node, "left")
        return BindingPattern(
            "default",
            members=(
                PatternMember(
                    None, javascript_pattern(target, source, path, depth + 1)
                ),
            ),
            fallback=javascript_value(field(node, "right"), source, path, depth + 1),
        )
    if node.type == "rest_pattern":
        inner = node.named_children[0] if node.named_children else None
        return BindingPattern(
            "rest",
            members=(
                PatternMember(None, javascript_pattern(inner, source, path, depth + 1)),
            ),
        )
    if node.type in {"object_pattern", "array_pattern"}:
        members: list[PatternMember] = []
        position = 0
        for child in node.children:
            if child.type == ",":
                position += 1
            if not child.is_named:
                continue
            if node.type == "array_pattern":
                members.append(
                    PatternMember(
                        position, javascript_pattern(child, source, path, depth + 1)
                    )
                )
                continue
            key = field(child, "key") or field(child, "left") or child
            target = field(child, "value") or child
            selector: str | int | None = text(key, source)
            if key.type == "computed_property_name":
                selector = (
                    parse_static_index_literal(text(key.named_children[0], source))
                    if key.named_children
                    else None
                )
            elif key.type in {"string", "number"}:
                selector = parse_static_index_literal(text(key, source))
            if child.type == "rest_pattern":
                selector = None
            members.append(
                PatternMember(
                    selector, javascript_pattern(target, source, path, depth + 1)
                )
            )
        return BindingPattern(
            "object" if node.type == "object_pattern" else "array",
            members=tuple(members),
        )
    return BindingPattern("unknown", reason=f"unsupported_binding_pattern:{node.type}")


@_cached_binding
def javascript_value(
    node: Any, source: bytes, path: str, depth: int = 0
) -> BindingValue:
    """将原始值保留为字段/位置结构及变量身份，不执行 getter、迭代器或用户代码。"""
    if node is None:
        return BindingValue(kind="missing")
    if depth > 24:
        return BindingValue(uncertain=True)
    origin = (_origin(node, path),)
    if node.type in {"arrow_function", "function_expression", "function_declaration", "generator_function_declaration"}:
        # 函数创建不会执行其体；捕获/回调结果需要显式边界，不冒充返回值。
        return BindingValue(origins=origin, uncertain=True)
    access = javascript_access_name(node, source)
    if access:
        return BindingValue(variables=(access,), origins=origin)
    if node.type in {"string", "number", "true", "false", "null", "undefined"}:
        return BindingValue(
            kind="missing" if node.type == "undefined" else "literal", origins=origin
        )
    if node.type in {"object", "array"}:
        members: dict[str | int, BindingValue] = {}
        position = 0
        uncertain = False
        extras: list[BindingValue] = []
        for child in node.children:
            if child.type == ",":
                position += 1
            if not child.is_named:
                continue
            if child.type == "spread_element":
                uncertain = True
                spread = javascript_value(
                    child.named_children[0] if child.named_children else None,
                    source,
                    path,
                    depth + 1,
                )
                extras.append(spread)
                # 动态 spread 可能覆写任意已知属性；保留 may 输入，不伪造旧字段仍为固定值。
                accumulated = (*extras, *members.values())
                variables = tuple(
                    dict.fromkeys(
                        name for value in accumulated for name in value.variables
                    )
                )
                origins = tuple(
                    dict.fromkeys(
                        origin for value in accumulated for origin in value.origins
                    )
                )
                members = {
                    key: BindingValue(
                        variables=variables, origins=origins, uncertain=True
                    )
                    for key in members
                }
                continue
            if node.type == "array":
                members[position] = javascript_value(child, source, path, depth + 1)
                continue
            key, value = field(child, "key"), field(child, "value")
            if child.type == "shorthand_property_identifier":
                key = value = child
            if key is None or value is None:
                uncertain = True
                continue
            selector: str | int | None = text(key, source)
            if key.type in {"number", "string"}:
                selector = parse_static_index_literal(text(key, source))
            elif key.type == "computed_property_name":
                selector = (
                    parse_static_index_literal(text(key.named_children[0], source))
                    if key.named_children
                    else None
                )
            if selector is None:
                uncertain = True
                extras.append(javascript_value(value, source, path, depth + 1))
                variables = tuple(
                    dict.fromkeys(
                        name
                        for item in (*extras, *members.values())
                        for name in item.variables
                    )
                )
                origins = tuple(
                    dict.fromkeys(
                        origin
                        for item in (*extras, *members.values())
                        for origin in item.origins
                    )
                )
                members = {
                    key: BindingValue(
                        variables=variables, origins=origins, uncertain=True
                    )
                    for key in members
                }
                continue
            members[selector] = javascript_value(value, source, path, depth + 1)
        values = (*extras, *members.values())
        if node.type == "array" and extras:
            return BindingValue(
                variables=tuple(
                    dict.fromkeys(name for item in values for name in item.variables)
                ),
                origins=tuple(
                    dict.fromkeys(
                        (
                            *origin,
                            *(origin for item in values for origin in item.origins),
                        )
                    )
                ),
                uncertain=True,
                diagnostics=("dynamic_array_spread_positions_not_resolved",),
            )
        return BindingValue(
            kind="object" if node.type == "object" else "array",
            members=tuple(ValueMember(key, value) for key, value in members.items()),
            variables=tuple(
                dict.fromkeys(name for value in values for name in value.variables)
            ),
            origins=tuple(
                dict.fromkeys(
                    (*origin, *(origin for value in values for origin in value.origins))
                )
            ),
            uncertain=uncertain,
        )
    # 调用名不是实参值；调用返回的变换不能假设为确定传递。
    if node.type in {"call_expression", "new_expression"}:
        args = field(node, "arguments")
        values = (
            [
                javascript_value(child, source, path, depth + 1)
                for child in args.named_children
            ]
            if args is not None
            else []
        )
        callee = field(node, "function")
        receiver = field(callee, "object")
        if receiver is not None:
            values.append(javascript_value(receiver, source, path, depth + 1))
    else:
        values = [
            javascript_value(child, source, path, depth + 1)
            for child in node.named_children
            if child.type not in {"property_identifier", "type_identifier"}
        ]
    return BindingValue(
        variables=tuple(
            dict.fromkeys(name for value in values for name in value.variables)
        ),
        origins=tuple(
            dict.fromkeys(
                (*origin, *(origin for value in values for origin in value.origins))
            )
        ),
        uncertain=node.type in {"call_expression", "new_expression"}
        or any(value.uncertain for value in values),
    )


@_cached_binding
def resolve_javascript_binding(
    target: Any, value: Any, source: bytes, path: str
) -> BindingResolution:
    """通过公共协议展开一对 JS/TS 目标和值，两个分析前端共享同一结果。"""
    return StructuredBindingResolver().resolve(
        javascript_pattern(target, source, path), javascript_value(value, source, path)
    )
