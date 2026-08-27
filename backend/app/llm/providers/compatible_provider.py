# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid

from backend.app.llm.base import ModelProvider, ModelResult, ModelTurn, ProviderCapabilities, ToolCall
from backend.app.llm.http import post_json


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI Chat Completions 兼容服务适配器。

    构造输入供应商名、基础 URL、模型和可选密钥；生成方法输入提示和工具 schema，
    输出统一模型结果。可用于 Ollama、vLLM 及其他兼容服务。
    """

    def __init__(self, *, provider_name: str, base_url: str, model: str, api_key: str = ""):
        """保存连接配置，不在构造阶段发起网络请求。"""
        self.name = provider_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """调用 ``/chat/completions`` 生成文本并输出规范化结果。"""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = await post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "temperature": 0.1,
            },
            headers,
            timeout=180.0,
        )
        choices = payload.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        if not text:
            raise RuntimeError("Compatible model response did not contain message content")
        return ModelResult(text=text, provider=self.name, model=self.model)

    def capabilities(self) -> ProviderCapabilities:
        """输出该适配器声明的工具调用能力。"""
        return ProviderCapabilities(streaming=False, tool_calling=True, structured_output=False)

    async def generate_with_tools(self, *, instructions: str, prompt: str, tools: list[dict]) -> ModelTurn:
        """将严格工具 schema 转为 Chat Completions 格式并输出规范化工具轮次。"""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        compatible_tools = [{
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
            },
        } for tool in tools]
        payload = await post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                "tools": compatible_tools,
                "tool_choice": "auto",
                "stream": False,
                "temperature": 0.1,
            },
            headers,
            timeout=180.0,
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
        return ModelTurn(
            text=content,
            provider=self.name,
            model=self.model,
            tool_calls=tuple(calls),
        )
