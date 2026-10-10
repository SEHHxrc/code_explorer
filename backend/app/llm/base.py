from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from backend.app.llm.response_metadata import ModelResponseMetadata
from backend.app.llm.conversation import ModelMessage, chat_messages, compatible_tools

if TYPE_CHECKING:
    from backend.app.llm.settings import ModelLimits


@dataclass(frozen=True)
class ModelResult:
    """普通文本生成结果；输出文本、供应商名和实际模型名。"""
    text: str
    provider: str
    model: str
    metadata: ModelResponseMetadata = field(default_factory=ModelResponseMetadata)


@dataclass(frozen=True)
class ProviderCapabilities:
    """模型供应商能力声明；输出流式、工具调用和结构化输出支持状态。"""
    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False


@dataclass(frozen=True)
class ToolCall:
    """一次规范化工具调用；输出调用 ID、工具名和已解析参数。"""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTurn:
    """带工具能力的一轮模型响应；输出文本、模型信息和零到多个工具调用。"""
    text: str
    provider: str
    model: str
    tool_calls: tuple[ToolCall, ...] = ()
    metadata: ModelResponseMetadata = field(default_factory=ModelResponseMetadata)
    continuation: ModelMessage | None = None


class ModelProvider(ABC):
    """所有在线和离线大模型适配器的异步统一接口。"""
    name: str
    model: str
    model_limits: ModelLimits

    @abstractmethod
    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        """生成纯文本。

        Args:
            instructions: 系统级约束和角色说明。
            prompt: 本轮用户内容及项目上下文。

        Returns:
            规范化的文本生成结果。
        """
        raise NotImplementedError

    def capabilities(self) -> ProviderCapabilities:
        """返回供应商能力；默认表示不支持扩展能力。"""
        return ProviderCapabilities()

    def request_payload(
        self, *, instructions: str, messages: list[ModelMessage], tools: list[dict[str, Any]],
        tool_choice: str = "auto", max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """构造待发送协议表示，供发送与预算共用；默认采用兼容 Chat 格式。"""
        payload: dict[str, Any] = {
            "model": self.model, "messages": chat_messages(instructions, messages),
            "stream": False, "max_tokens": max_output_tokens or getattr(self, "max_output_tokens", 2400),
        }
        if tools:
            payload.update(tools=compatible_tools(tools), tool_choice=tool_choice)
        return payload

    async def generate_with_tools(
        self, *, instructions: str, prompt: str = "", tools: list[dict[str, Any]],
        messages: list[ModelMessage] | None = None, tool_choice: str = "auto",
        max_output_tokens: int | None = None, transient_retries: int = 2,
        timeout: float | None = None,
    ) -> ModelTurn:
        """生成可能包含工具调用的一轮响应。

        输入系统说明、提示词和工具 JSON Schema；默认回退为纯文本生成，输出不含
        工具调用的 :class:`ModelTurn`，支持工具的供应商应覆盖本方法。
        """
        if tools or (messages and len(messages) != 1):
            raise ValueError("This provider does not support native tool conversations")
        result = await self.generate(instructions=instructions, prompt=(messages[0].get("content") or "") if messages else prompt)
        return ModelTurn(text=result.text, provider=result.provider, model=result.model, metadata=result.metadata)
