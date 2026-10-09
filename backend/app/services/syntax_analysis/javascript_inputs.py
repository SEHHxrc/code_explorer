"""JS/TS 常见回调输入的静态绑定；不运行框架、不将任意回调视为 Source。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .callback_inputs import CallbackParameterInput
from .javascript import JAVASCRIPT_FUNCTION_NODES, javascript_function
from .tree_parser import field, text


def project_uses_react(project_root: Path, path: str) -> bool:
    """只读取项目内最近的 package.json；单独 React 依赖提供 JSX 运行库线索。

    输入项目根目录与相对源码路径，返回是否有明确 React 依赖且无 Vue/Preact
    歧义。损坏、超限、符号链接越界或无配置时返回 False，不猜测组件类型。
    """
    root = project_root.resolve()
    current = (root / path).resolve().parent
    while current == root or root in current.parents:
        package = current / "package.json"
        try:
            if package.exists():
                resolved = package.resolve()
                resolved.relative_to(root)
                if resolved.stat().st_size > 64 * 1024:
                    return False
                data = json.loads(resolved.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    return False
                dependencies = set()
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    group = data.get(key, {})
                    if isinstance(group, dict):
                        dependencies.update(group)
                return "react" in dependencies and not dependencies.intersection(
                    {"vue", "preact"}
                )
        except (OSError, UnicodeError, ValueError):
            return False
        if current == root:
            break
        current = current.parent
    return False


def javascript_scope_nodes(root: Any, source: bytes) -> Iterator[Any]:
    """遍历当前词法作用域，保留函数声明节点但不进入其函数体。"""
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        if node.type in JAVASCRIPT_FUNCTION_NODES:
            if javascript_function(node, source) is not None:
                continue
        pending.extend(reversed(node.named_children))


def javascript_callback_inputs(
    root: Any,
    source: bytes,
    *,
    react: bool,
    qualify: Callable[[Any], str],
) -> tuple[CallbackParameterInput, ...]:
    """匹配原生表单 JSX 事件及已确认 IncomingMessage 的 data 回调。

    输入当前作用域 AST、源码、React 依据及既有接收者解析器，返回有明确注册
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
        if react and node.type == "jsx_attribute" and node.named_children:
            tag = text(field(node.parent, "name"), source)
            attribute = text(node.named_children[0], source)
            if tag in {"input", "textarea", "select"} and attribute in {
                "onChange",
                "onInput",
            }:
                expression = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "jsx_expression"
                    ),
                    None,
                )
                value = (
                    expression.named_children[0]
                    if expression is not None and expression.named_children
                    else None
                )
                type_name = "react.DOM.InputEvent"
        elif node.type == "call_expression":
            name = qualify(field(node, "function"))
            receiver_key = text(field(field(node, "function"), "object"), source)
            arguments = field(node, "arguments")
            items = arguments.named_children if arguments is not None else []
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
