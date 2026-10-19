"""Chat Completions SSE 的有界组装器；不执行工具，不纠正结束状态或推断用量。"""

from __future__ import annotations

import json
from typing import Any


class StreamProtocolError(ValueError):
    """流式帧不符合约定；异常只包含固定说明，不包含供应商正文。"""


class ChatStream:
    """组装首个 choice 的文本、原生推理续接和按 index 分片的工具调用。"""

    def __init__(self) -> None:
        """初始化当前请求的内存缓冲；隐藏推理只能经审计的可见投影保存。"""
        self.payload: dict[str, Any] = {"choices": [{"index": 0, "message": {"role": "assistant"}, "finish_reason": None}]}
        self.fragments: dict[str, list[str]] = {}
        self.calls: dict[int, dict[str, Any]] = {}
        self.done = False
        self.events = 0

    def consume(self, data: str) -> set[str]:
        """输入一条完整 data 事件，返回本帧非空文本通道名；保留供应商终止与 usage。"""
        if data == "[DONE]":
            self.done = True
            return set()
        event = json.loads(data)
        if not isinstance(event, dict) or event.get("error"):
            raise StreamProtocolError("模型返回无效或错误 SSE 事件。")
        self.events += 1
        for key in ("id", "model", "usage"):
            if event.get(key) is not None:
                self.payload[key] = event[key]
        channels = set()
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise StreamProtocolError("模型 delta 必须为对象。")
            for key in ("content", "reasoning_content", "thinking", "refusal"):
                value = delta.get(key)
                if value is not None and not isinstance(value, str):
                    raise StreamProtocolError("模型文本分片类型错误。")
                if value:
                    self.fragments.setdefault(key, []).append(value)
                    channels.add(key)
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict) or type(call.get("index")) is not int or not 0 <= call["index"] < 128:
                    raise StreamProtocolError("模型工具调用索引无效。")
                target = self.calls.setdefault(call["index"], {"type": "function", "function": {"name": [], "arguments": []}})
                if call.get("type", "function") != "function":
                    raise StreamProtocolError("不支持该流式工具类型。")
                if call.get("id"):
                    if not isinstance(call["id"], str) or target.get("id", call["id"]) != call["id"]:
                        raise StreamProtocolError("模型工具调用 ID 冲突。")
                    target["id"] = call["id"]
                function = call.get("function") or {}
                for key in ("name", "arguments"):
                    value = function.get(key)
                    if value is not None and not isinstance(value, str):
                        raise StreamProtocolError("模型工具调用分片类型错误。")
                    if value:
                        target["function"][key].append(value)
            reason = choice.get("finish_reason")
            if reason is not None:
                saved = self.payload["choices"][0]["finish_reason"]
                if not isinstance(reason, str) or (saved is not None and saved != reason):
                    raise StreamProtocolError("模型终止状态冲突。")
                self.payload["choices"][0]["finish_reason"] = reason
        return channels

    def finish(self) -> dict[str, Any]:
        """仅在收到 DONE 和真实终止原因时返回完整响应；不把 EOF 当作 stop。"""
        if not self.done or self.payload["choices"][0]["finish_reason"] is None:
            raise StreamProtocolError("流式响应缺少结束标记或终止原因。")
        message = self.payload["choices"][0]["message"]
        message.update({key: "".join(values) for key, values in self.fragments.items()})
        if self.calls:
            calls = []
            for _, call in sorted(self.calls.items()):
                function = {key: "".join(values) for key, values in call["function"].items()}
                if not call.get("id") or not function["name"]:
                    raise StreamProtocolError("流式工具调用缺少 ID 或名称。")
                try:
                    arguments = json.loads(function["arguments"] or "{}")
                except json.JSONDecodeError:
                    raise StreamProtocolError("流式工具参数不是完整 JSON。") from None
                if not isinstance(arguments, dict):
                    raise StreamProtocolError("流式工具参数必须为对象。")
                calls.append({"id": call["id"], "type": "function", "function": function})
            message["tool_calls"] = calls
        return self.payload
