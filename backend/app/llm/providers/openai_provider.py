# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid

from backend.app.llm.base import ModelProvider, ModelResult, ModelTurn, ProviderCapabilities, ToolCall
from backend.app.llm.http import post_json


class OpenAIResponsesProvider(ModelProvider):
    """OpenAI Responses API 适配器。

    构造输入 API 密钥、模型和基础 URL；生成方法输入系统说明、用户提示及可选工具，
    输出统一文本结果或工具调用轮次。
    """
    name = "openai"

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1"):
        """保存连接配置，不在构造阶段发起网络请求。"""
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """调用 Responses API 生成文本并输出规范化结果。"""
        payload = await post_json(
            f"{self.base_url}/responses",
            {
                "model": self.model,
                "instructions": instructions,
                "input": prompt,
                "store": False,
                "max_output_tokens": 2400,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
        )
        text = payload.get("output_text") or self._extract_output_text(payload)
        if not text:
            raise RuntimeError("Model response did not contain output text")
        return ModelResult(text=text, provider=self.name, model=self.model)

    def capabilities(self) -> ProviderCapabilities:
        """输出 Responses API 的工具调用和结构化输出能力。"""
        return ProviderCapabilities(streaming=False, tool_calling=True, structured_output=True)

    async def generate_with_tools(self, *, instructions: str, prompt: str, tools: list[dict]) -> ModelTurn:
        """调用带函数工具的 Responses API 并输出文本及规范化工具调用。"""
        payload = await post_json(
            f"{self.base_url}/responses",
            {
                "model": self.model,
                "instructions": instructions,
                "input": prompt,
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "store": False,
                "max_output_tokens": 2400,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
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
            model=self.model,
            tool_calls=tuple(calls),
        )

    @staticmethod
    def _extract_output_text(payload: dict) -> str:
        """输入原始 Responses 载荷，输出所有消息文本片段的合并结果。"""
        chunks: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    chunks.append(content["text"])
        return "\n".join(chunks)
