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
from backend.app.llm.conversation import ModelMessage, response_items
from backend.app.llm.budget import validate_provider_request


class OpenAIResponsesProvider(ModelProvider):
    """OpenAI Responses API 适配器。

    构造输入 API 密钥、模型和基础 URL；生成方法输入系统说明、用户提示及可选工具，
    输出统一文本结果或工具调用轮次。
    """
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        max_output_tokens: int = 2400,
    ) -> None:
        """保存连接配置，不在构造阶段发起网络请求。"""
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max_output_tokens
        from backend.app.llm.settings import get_model_limits
        self.model_limits = replace(get_model_limits(model), max_output_tokens=max_output_tokens)

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """调用 Responses API 生成文本并输出规范化结果。"""
        turn = await self.generate_with_tools(instructions=instructions, prompt=prompt, tools=[])
        return ModelResult(
            text=turn.text, provider=turn.provider, model=turn.model, metadata=turn.metadata,
        )

    def capabilities(self) -> ProviderCapabilities:
        """输出 Responses API 的工具调用和结构化输出能力。"""
        return ProviderCapabilities(streaming=False, tool_calling=True, structured_output=True)

    def request_payload(
        self, *, instructions: str, messages: list[ModelMessage], tools: list[dict[str, Any]],
        tool_choice: str = "auto", max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """序列化完整 Responses 会话；无服务端会话存储，申请加密推理续接状态。"""
        payload = {
            "model": self.model, "instructions": instructions, "input": response_items(messages),
            "store": False, "include": ["reasoning.encrypted_content"],
            "max_output_tokens": max_output_tokens or self.max_output_tokens,
        }
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice, parallel_tool_calls=False)
        return payload

    async def generate_with_tools(
        self, *, instructions: str, prompt: str = "", tools: list[dict[str, Any]],
        messages: list[ModelMessage] | None = None, tool_choice: str = "auto",
        max_output_tokens: int | None = None, transient_retries: int = 2,
        timeout: float | None = None,
    ) -> ModelTurn:
        """调用带函数工具的 Responses API 并输出文本及规范化工具调用。"""
        request = self.request_payload(
            instructions=instructions,
            messages=messages if messages is not None else [ModelMessage(role="user", content=prompt)],
            tools=tools, tool_choice=tool_choice, max_output_tokens=max_output_tokens,
        )
        validate_provider_request(self, request)
        payload = await post_json(
            f"{self.base_url}/responses",
            request,
            {"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout or 120.0, transient_retries=transient_retries,
        )
        calls: list[ToolCall] = []
        for item in payload.get("output", []):
            if item.get("type") != "function_call" or not item.get("name"):
                continue
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(
                id=item.get("call_id") or item.get("id") or uuid.uuid4().hex,
                name=item["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            ))
        return ModelTurn(
            text=payload.get("output_text") or self._extract_output_text(payload),
            provider=self.name,
            model=payload.get("model") or self.model,
            tool_calls=tuple(calls),
            metadata=response_metadata(payload, responses_api=True),
            continuation=ModelMessage(role="assistant", content=payload.get("output_text") or self._extract_output_text(payload),
                                      provider_items=[item for item in payload.get("output", []) if isinstance(item, dict)],
                                      tool_calls=[{"id": call.id, "type": "function", "function": {
                                          "name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                      }} for call in calls]),
        )

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        """输入原始 Responses 载荷，输出所有消息文本片段的合并结果。"""
        chunks = [
            content["text"]
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text" and content.get("text")
        ]
        return "\n".join(chunks)
