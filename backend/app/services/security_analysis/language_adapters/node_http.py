"""Node.js 请求体回调绑定适配；不模拟完整堆、闭包或事件循环。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from backend.app.services.syntax_analysis.callback_inputs import CallbackParameterInput
from backend.app.services.syntax_analysis.javascript import (
    JAVASCRIPT_FUNCTION_NODES,
    javascript_function,
    javascript_parameters,
    javascript_scope_nodes,
)
from backend.app.services.syntax_analysis.javascript_bindings import (
    javascript_pattern,
    javascript_value,
)
from backend.app.services.syntax_analysis import field, text
from backend.app.services.value_binding import BindingValue, StructuredBindingResolver

from ..ir import IRLocation, IRValueBoundary


def _bindings(root: Any, source: bytes) -> set[str]:
    """读取当前作用域声明名，排除请求体捕获变量的遮蔽。"""
    result: set[str] = set()
    for node in javascript_scope_nodes(root, source):
        if node.type in {
            "variable_declarator",
            "function_declaration",
            "class_declaration",
        }:
            pattern = javascript_pattern(field(node, "name"), source, "")
            result.update(
                item.output
                for item in StructuredBindingResolver()
                .resolve(pattern, BindingValue())
                .projections
            )
    return result


def collect_request_body_boundaries(
    root: Any,
    source: bytes,
    path: str,
    scope: IRLocation,
    registrations: tuple[CallbackParameterInput, ...],
    *,
    location: Callable[[Any], IRLocation],
) -> tuple[list[IRValueBoundary], tuple[str, ...]]:
    """提取同一请求 data→end 的局部字符串/数组槽位绑定及覆盖缺口。

    输入已确认的回调注册和原始源码位置，输出通用值边界；不模拟事件循环、
    完整 Buffer 解码或任意闭包。字符串使用 CFG 出口值，数组只支持直线追加。
    """
    types = {item.type_name for item in registrations}
    if not {"node.IncomingMessage.bodyChunk", "node.IncomingMessage.endCallback"}.issubset(types):
        return [], ()
    nodes = list(javascript_scope_nodes(root, source))
    callbacks: dict[tuple[int, int], Any] = {}
    pending = [root]
    while pending:
        node = pending.pop()
        parts = (
            javascript_function(node, source)
            if node.type in JAVASCRIPT_FUNCTION_NODES
            else None
        )
        if parts is not None:
            target = parts[1]
            callbacks[(target.start_byte, target.end_byte)] = target
        pending.extend(node.named_children)
    result: list[IRValueBoundary] = []
    gaps: list[str] = []

    def add(
        kind: str,
        writer: Any,
        names: tuple[str, ...],
        callback: Any,
        reader: Any,
        outputs: tuple[str, ...],
        capture: bool,
        limits: tuple[str, ...],
        *,
        at_exit: bool = False,
    ) -> None:
        """稳定去重身份只来自原始位置和槽位，不依赖自然语言或模型。"""
        if not names or not outputs or len(result) >= 128:
            return
        source_location, target_location = (
            location(callback if at_exit else writer),
            location(reader),
        )
        material = "\x1f".join(
            (
                kind,
                source_location.location_id,
                target_location.location_id,
                *names,
                *outputs,
            )
        )
        result.append(
            IRValueBoundary(
                "value-boundary:"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
                kind,
                location(callback),
                source_location,
                names,
                location(reader) if capture else scope,
                target_location,
                outputs,
                capture,
                limits,
                at_exit,
            )
        )

    def shadowed(callback: Any) -> set[str]:
        """返回参数和局部声明，保留请求体回调对外层变量的写入。"""
        body = field(callback, "body")
        values = _bindings(body, source)
        for _, _, node in javascript_parameters(callback, source):
            values.update(
                item.output
                for item in StructuredBindingResolver()
                .resolve(javascript_pattern(node, source, path), BindingValue())
                .projections
            )
        return values

    # 只绑定同一外层函数实际声明的字符串或空数组，不靠变量名猜测 body。
    slots: dict[str, str] = {}
    for statement in root.named_children:
        if statement.type not in {"lexical_declaration", "variable_declaration"}:
            continue
        for node in statement.named_children:
            name, value = field(node, "name"), field(node, "value")
            if name is None or name.type != "identifier" or value is None:
                continue
            if value.type == "array" and not value.named_children:
                slots[text(name, source)] = "array"
            elif value.type == "string" and text(statement, source).lstrip().startswith(
                ("let ", "var ")
            ):
                slots[text(name, source)] = "scalar"
    readers = [
        item
        for item in registrations
        if item.type_name == "node.IncomingMessage.endCallback"
    ]
    for registration in registrations:
        callback = callbacks.get(registration.callback_span)
        if (
            registration.type_name != "node.IncomingMessage.bodyChunk"
            or callback is None
        ):
            continue
        body = field(callback, "body")
        items = list(javascript_scope_nodes(body, source))
        controlled = any(
            item.type
            in {
                "if_statement",
                "for_statement",
                "while_statement",
                "try_statement",
                "switch_statement",
            }
            for item in items
        )
        shadows = shadowed(callback)
        scalar_writes: dict[str, Any] = {}
        array_writes: list[tuple[str, Any, tuple[str, ...]]] = []
        for node in items:
            if node.type in {
                "assignment_expression",
                "augmented_assignment_expression",
            }:
                name = text(field(node, "left"), source)
                if name in slots and name not in shadows and slots[name] == "scalar":
                    scalar_writes[name] = node
            elif node.type == "call_expression":
                callee, args = field(node, "function"), field(node, "arguments")
                receiver = text(field(callee, "object"), source)
                if (
                    text(field(callee, "property"), source) == "push"
                    and slots.get(receiver) == "array"
                    and receiver not in shadows
                    and args is not None
                ):
                    values = tuple(
                        dict.fromkeys(
                            name
                            for item in args.named_children
                            for name in javascript_value(item, source, path).variables
                        )
                    )
                    array_writes.append((receiver, node, values))
        rebound_arrays = {
            text(field(item, "left"), source)
            for item in items
            if item.type in {"assignment_expression", "augmented_assignment_expression"}
            and slots.get(text(field(item, "left"), source)) == "array"
        }
        if rebound_arrays:
            gaps.append("javascript_shared_array_rebinding_not_modeled")
            array_writes = [
                item for item in array_writes if item[0] not in rebound_arrays
            ]
        if controlled and array_writes:
            gaps.append("javascript_shared_array_control_flow_not_modeled")
            array_writes = []
        for reader in readers:
            target = callbacks.get(reader.callback_span)
            if (
                target is None
                or not reader.receiver_key
                or reader.receiver_key != registration.receiver_key
            ):
                continue
            hidden = shadowed(target)
            for name, node, names in (
                *((name, node, (name,)) for name, node in scalar_writes.items()),
                *array_writes,
            ):
                if name not in hidden:
                    add(
                        "request_body_state",
                        node,
                        names,
                        callback,
                        target,
                        (name,),
                        True,
                        (
                            "请求 data→end 的局部槽位连接按 may 处理；未模拟事件异常、销毁、其他写入回调或完整 Buffer/堆别名。",
                        ),
                        at_exit=slots[name] == "scalar",
                    )
    if len(result) >= 128:
        gaps.append("javascript_value_boundary_budget_exceeded")
    return result, tuple(dict.fromkeys(gaps))
