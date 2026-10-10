from __future__ import annotations

import json
import uuid
from typing import Any
from dataclasses import replace

from backend.app.llm.base import (
    ModelProvider,
    ModelResult,
    ModelTurn,
    ProviderCapabilities,
    ToolCall,
)
from backend.app.llm.http import post_json
from backend.app.llm.response_metadata import response_metadata
from backend.app.llm.conversation import ModelMessage
from backend.app.llm.budget import validate_provider_request


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI Chat Completions 兼容服务适配器。

    构造输入供应商名、基础 URL、模型和可选密钥；生成方法输入提示和工具 schema，
    输出统一模型结果。可用于 Ollama、vLLM 及其他兼容服务。
    """

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        max_output_tokens: int = 2400,
        output_token_parameter: str = "max_tokens",
        reasoning_effort: str | None = None,
    ) -> None:
        """保存连接配置，不在构造阶段发起网络请求。"""
        self.name = provider_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        if output_token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported output token parameter")
        self.output_token_parameter = output_token_parameter
        self.reasoning_effort = reasoning_effort
        from backend.app.llm.settings import get_model_limits
        self.model_limits = replace(get_model_limits(model), max_output_tokens=max_output_tokens)

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """调用 ``/chat/completions`` 生成文本并输出规范化结果。"""
        turn = await self.generate_with_tools(instructions=instructions, prompt=prompt, tools=[])
        return ModelResult(
            text=turn.text, provider=turn.provider, model=turn.model, metadata=turn.metadata,
        )

    def capabilities(self) -> ProviderCapabilities:
        """输出该适配器声明的工具调用能力。"""
        return ProviderCapabilities(streaming=False, tool_calling=True, structured_output=False)

    def request_payload(
        self, *, instructions: str, messages: list[ModelMessage], tools: list[dict[str, Any]],
        tool_choice: str = "auto", max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """构造原生会话请求；输出参数和思考强度按显式配置选择，不猜测模型别名。"""
        payload = super().request_payload(instructions=instructions, messages=messages, tools=tools,
                                          tool_choice=tool_choice, max_output_tokens=max_output_tokens)
        payload[self.output_token_parameter] = payload.pop("max_tokens")
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    async def generate_with_tools(
        self, *, instructions: str, prompt: str = "", tools: list[dict[str, Any]],
        messages: list[ModelMessage] | None = None, tool_choice: str = "auto",
        max_output_tokens: int | None = None, transient_retries: int = 2,
        timeout: float | None = None,
    ) -> ModelTurn:
        """将严格工具 schema 转为 Chat Completions 格式并输出规范化工具轮次。"""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        conversation = messages if messages is not None else [ModelMessage(role="user", content=prompt)]
        request = self.request_payload(instructions=instructions, messages=conversation, tools=tools,
                                       tool_choice=tool_choice, max_output_tokens=max_output_tokens)
        validate_provider_request(self, request)
        payload = await post_json(
            f"{self.base_url}/chat/completions",
            request,
            headers,
            timeout=timeout or 180.0, transient_retries=transient_retries,
        )
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if function.get("name"):
                calls.append(ToolCall(
                    id=call.get("id") or uuid.uuid4().hex,
                    name=function["name"],
                    arguments=arguments if isinstance(arguments, dict) else {},
                ))
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        continuation = ModelMessage(role="assistant", content=content or None)
        if calls:
            continuation["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False),
            }} for call in calls]
        if isinstance(message.get("reasoning_content"), str):
            continuation["reasoning_content"] = message["reasoning_content"]
        return ModelTurn(
            text=content,
            provider=self.name,
            model=payload.get("model") or self.model,
            tool_calls=tuple(calls),
            metadata=response_metadata(payload, responses_api=False),
            continuation=continuation,
        )
