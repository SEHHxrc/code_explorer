"""Node.js HTTP 回调输入的静态绑定；不运行框架、不将任意回调视为 Source。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .callback_inputs import CallbackParameterInput
from .javascript import JAVASCRIPT_FUNCTION_NODES, javascript_function, javascript_scope_nodes
from .tree_parser import field, text, semantic_children






def node_request_inputs(
    root: Any,
    source: bytes,
    *,
    qualify: Callable[[Any], str],
) -> tuple[CallbackParameterInput, ...]:
    """匹配 Express/HTTP handler 及已确认 IncomingMessage 的 data/end 回调。

    输入当前作用域 AST、源码及既有接收者解析器，返回有明确注册
    点的参数绑定。具名回调只在本作用域内唯一、未重新绑定时关联；未支持的
    包装器、别名、跨作用域引用不猜测。这里不模拟事件循环或状态更新。
    """
    nodes = list(javascript_scope_nodes(root, source))
    functions: dict[str, list[Any]] = {}
    written: set[str] = set()
    for node in nodes:
        parts = (
            javascript_function(node, source)
            if node.type in JAVASCRIPT_FUNCTION_NODES
            else None
        )
        if parts is not None:
            name, target = parts
            functions.setdefault(name, []).append(target)
        elif node.type in {"assignment_expression", "augmented_assignment_expression"}:
            written.add(text(field(node, "left"), source))
        elif node.type == "update_expression":
            written.add(text(field(node, "argument"), source))

    def callback(value: Any) -> Any | None:
        """内联函数或唯一、未改写的本地具名函数才具有确定绑定。"""
        parts = (
            javascript_function(value, source)
            if value.type in JAVASCRIPT_FUNCTION_NODES
            else None
        )
        if parts is not None:
            return parts[1]
        if value.type == "identifier":
            name = text(value, source)
            candidates = functions.get(name, [])
            if len(candidates) == 1 and name not in written:
                return candidates[0]
        return None

    result: list[CallbackParameterInput] = []
    for node in nodes:
        value, type_name = None, ""
        receiver_key = ""
        limitations: tuple[str, ...] = ()
        if node.type == "call_expression":
            name = qualify(field(node, "function"))
            receiver_key = text(field(field(node, "function"), "object"), source)
            arguments = field(node, "arguments")
            items = semantic_children(arguments)
            if name in {"http.createServer", "https.createServer"} and items:
                value, type_name = items[-1], "node.IncomingMessage"
            elif (
                name.startswith("express.Application.")
                and name.rsplit(".", 1)[-1]
                in {"get", "post", "put", "patch", "delete", "head", "options", "all"}
                and len(items) >= 2
            ):
                value, type_name = items[-1], "express.Request"
                if len(items) != 2:
                    limitations = ("express_middleware_chain_not_modeled",)
            if (
                name
                in {
                    "node.IncomingMessage.on",
                    "node.IncomingMessage.once",
                    "node.IncomingMessage.addListener",
                }
                and len(items) == 2
                and items[0].type == "string"
                and text(items[0], source).strip("'\"") in {"data", "end"}
            ):
                event = text(items[0], source).strip("'\"")
                value, type_name = items[1], "node.IncomingMessage.bodyChunk" if event == "data" else "node.IncomingMessage.endCallback"
        target = callback(value) if value is not None else None
        if target is not None:
            result.append(
                CallbackParameterInput(
                    (target.start_byte, target.end_byte),
                    0,
                    type_name,
                    (node.start_byte, node.end_byte),
                    limitations,
                    receiver_key,
                )
            )
    return tuple(result)
