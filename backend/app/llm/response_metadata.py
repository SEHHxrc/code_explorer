"""规范化供应商终止状态与真实用量；不推算缺失数据、不保存原始响应正文。"""

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class ModelUsage:
    """供应商报告的 Token 数；None 表示未知，0 表示确实报告为零。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponseMetadata:
    """一轮返回的传输状态，不等同于报告完整或安全审查覆盖完整。"""

    response_id: str | None = None
    finish_reason: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None
    refused: bool = False
    usage: ModelUsage = field(default_factory=ModelUsage)


def response_channel_sizes(payload: dict[str, Any]) -> dict[str, Any]:
    """仅计量响应文本通道是否存在与字符数，不保存正文、不推算推理 Token。"""
    messages = []
    choices = payload.get("choices")
    for choice in choices if isinstance(choices, list) else []:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        messages.append({"message_utf8_bytes": len(json.dumps(message, ensure_ascii=False).encode("utf-8")),
                         "fields": {name: {"present": name in message, "characters": len(message[name])
                               if isinstance(message.get(name), str) else None}
                         for name in ("content", "reasoning_content", "thinking")}})
    return {"measurement": "protocol_field_characters_not_token_accounting", "chat_messages": messages}


def response_metadata(payload: dict[str, Any], *, responses_api: bool) -> ModelResponseMetadata:
    """输入供应商 JSON 与协议种类，输出允许列表内的终止、拒绝和用量字段。"""
    def object_fields(value: Any) -> dict[str, Any]:
        """可选元数据字段格式异常时保持未知，不丢弃有效模型回答。"""
        return value if isinstance(value, dict) else {}

    usage = object_fields(payload.get("usage"))
    choices = payload.get("choices") or []
    choice = object_fields(choices[0]) if isinstance(choices, list) and choices else {}
    message = object_fields(choice.get("message"))
    incomplete = object_fields(payload.get("incomplete_details"))
    input_key, output_key = ("input_tokens", "output_tokens") if responses_api else ("prompt_tokens", "completion_tokens")
    alternate_input, alternate_output = ("prompt_tokens", "completion_tokens") if responses_api else ("input_tokens", "output_tokens")
    input_details = object_fields(usage.get(f"{input_key}_details", usage.get(f"{alternate_input}_details")))
    output_details = object_fields(usage.get(f"{output_key}_details", usage.get(f"{alternate_output}_details")))

    def count(value: Any) -> int | None:
        """仅接受供应商返回的非负整数，拒绝 bool、字符串及估算值。"""
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    return ModelResponseMetadata(
        response_id=payload.get("id"),
        finish_reason=choice.get("finish_reason") if not responses_api else None,
        response_status=payload.get("status") if responses_api else None,
        incomplete_reason=incomplete.get("reason"),
        refused=bool(message.get("refusal")) or any(
            object_fields(content).get("type") == "refusal"
            for item in payload.get("output") or []
            for content in object_fields(item).get("content") or []
        ),
        usage=ModelUsage(
            input_tokens=count(usage.get(input_key, usage.get(alternate_input))),
            output_tokens=count(usage.get(output_key, usage.get(alternate_output))),
            total_tokens=count(usage.get("total_tokens")),
            cached_input_tokens=count(input_details.get("cached_tokens")),
            reasoning_tokens=count(output_details.get("reasoning_tokens")),
        ),
    )
