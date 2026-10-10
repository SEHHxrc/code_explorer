"""供应商无关的工具会话契约与两种 API 序列化；不执行工具、不持久化推理内容。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from typing_extensions import Required


class ModelMessage(TypedDict, total=False):
    """一条会话消息；工具结果必须带匹配的 tool_call_id。

    reasoning_content/provider_items 只在本次运行内存中保留，供供应商续接协议使用；
    不进入前端事件、报告、证据或实验持久化。不能将它们作为源码事实。
    """

    role: Required[Literal["user", "assistant", "tool"]]
    content: str | None
    tool_call_id: str
    tool_calls: list[dict[str, Any]]
    reasoning_content: str
    provider_items: list[dict[str, Any]]


def validate_messages(messages: list[ModelMessage]) -> None:
    """校验调用/结果配对；拒绝孤立结果、重复 ID 和未完成的调用轮次。"""
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            call_id = message.get("tool_call_id", "")
            if call_id not in pending:
                raise ValueError("Tool result has no matching pending call")
            pending.remove(call_id)
            continue
        if pending:
            raise ValueError("Every tool call must receive a result before the next message")
        if role not in {"user", "assistant"}:
            raise ValueError("Invalid conversation message role")
        if role != "assistant" and message.get("tool_calls"):
            raise ValueError("Only assistant messages may request tools")
        for call in message.get("tool_calls") or []:
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in seen:
                raise ValueError("Tool call IDs must be nonempty and unique")
            pending.add(call_id)
            seen.add(call_id)
    if pending:
        raise ValueError("Conversation ends with unanswered tool calls")


def chat_messages(instructions: str, messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """转换为 Chat Completions 消息；仅透传本协议的允许字段。"""
    validate_messages(messages)
    result: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
    for message in messages:
        result.append({key: value for key, value in message.items() if key in {
            "role", "content", "tool_calls", "tool_call_id", "reasoning_content",
        }})
    return result


def response_items(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """转换为 Responses 输入；保留原生 output 项及加密推理续接状态。"""
    validate_messages(messages)
    result: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "tool":
            result.append({"type": "function_call_output", "call_id": message.get("tool_call_id"),
                           "output": message.get("content") or ""})
        elif message.get("provider_items"):
            result.extend(message.get("provider_items") or [])
        elif message["role"] == "assistant" and message.get("tool_calls"):
            for call in message.get("tool_calls") or []:
                result.append({"type": "function_call", "call_id": call["id"],
                               "name": call["function"]["name"],
                               "arguments": call["function"]["arguments"]})
        else:
            result.append({"role": message["role"], "content": message.get("content") or ""})
    return result


def compatible_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """转换公共函数 schema；与预算计数和实际发送共用同一表示。"""
    return [{"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""),
        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
    }} for tool in tools]
