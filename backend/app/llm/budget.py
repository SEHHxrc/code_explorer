"""唯一请求预算规划入口；上下文构造、Agent 和实验预检使用同一策略。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
import json
from typing import TYPE_CHECKING, Any, TypeVar

from backend.app.llm.tokens import RequestTokenCounter, TokenEstimate, get_token_counter
from backend.app.llm.base import ModelProvider
from backend.app.llm.conversation import ModelMessage, chat_messages, compatible_tools

if TYPE_CHECKING:
    from backend.app.llm.settings import ModelLimits

ContextValue = TypeVar("ContextValue")


class ModelBudgetError(ValueError):
    """应用预算不足；与平台网关 503、连接失败和服务端超窗错误区分。"""


def request_payload(
    provider: Any, *, instructions: str, messages: list[ModelMessage], tools: list[dict[str, Any]],
    model: str = "", max_output_tokens: int = 2400, tool_choice: str = "auto",
) -> dict[str, Any]:
    """调用真实适配器的纯序列化器；未配置/测试替身使用标准 Chat 表示。"""
    if isinstance(provider, ModelProvider):
        return provider.request_payload(instructions=instructions, messages=messages, tools=tools,
                                        max_output_tokens=max_output_tokens, tool_choice=tool_choice)
    payload: dict[str, Any] = {"model": model, "messages": chat_messages(instructions, messages),
                               "stream": False, "max_tokens": max_output_tokens}
    if tools:
        payload.update(tools=compatible_tools(tools), tool_choice=tool_choice)
    return payload


def initial_message(question: str, context: str) -> ModelMessage:
    """两组/普通 Agent 共用问题与静态上下文包装；不加入模型生成的摘要。"""
    return ModelMessage(role="user", content=f"USER_QUESTION\n{question}\n\nTRUSTED_STATIC_CONTEXT\n{context}")


def validate_provider_request(provider: ModelProvider, payload: dict[str, Any]) -> None:
    """适配器最后一道发送前检查，覆盖普通文本生成；使用实例冻结的配置。"""
    output = next((payload[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
                   if key in payload), 2400)
    limits = replace(provider.model_limits, max_output_tokens=output)
    BudgetPlanner(limits, model=provider.model).require(payload)


class BudgetPlanner:
    """限制每次请求输入，并在已知窗口内预留最大输出和模板安全余量。"""

    def __init__(self, limits: ModelLimits, *, model: str = "", counter: RequestTokenCounter | None = None) -> None:
        """注入模型限制和可替换计数器；未知窗口不会伪造为应用预算。"""
        self.limits = limits
        self.counter = counter or get_token_counter(model, limits.tokenizer, limits.tokenizer_path)
        allowed = limits.max_input_tokens
        if limits.context_window_tokens is not None:
            allowed = min(allowed, limits.context_window_tokens - limits.max_output_tokens)
        self.input_limit = allowed - limits.safety_margin_tokens
        if self.input_limit <= 0:
            raise ModelBudgetError("Configured model window cannot reserve output and safety margin")

    def estimate(self, payload: dict[str, Any]) -> TokenEstimate:
        """计数待发送的实际协议表示，不把字符数或推测值记作真实 usage。"""
        return self.counter.request(payload)

    def fits(self, payload: dict[str, Any]) -> bool:
        """判断输入 Token 估算和可选的旧字符保护是否同时满足。"""
        return self.estimate(payload).tokens <= self.input_limit and self._within_char_limit(payload)

    def _within_char_limit(self, payload: dict[str, Any]) -> bool:
        """仅在旧字符保护启用时序列化计数字符，关闭时不重复处理整个请求。"""
        return not self.limits.max_context_chars or len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ) <= self.limits.max_context_chars

    def require(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调用前拒绝超预算请求，返回可安全持久化的预算快照。"""
        estimate = self.estimate(payload)
        if estimate.tokens > self.input_limit or not self._within_char_limit(payload):
            raise ModelBudgetError("Request exceeds application input budget; increase budget or request less evidence")
        return {
            "input_token_estimate": asdict(estimate),
            "input_token_budget": self.input_limit,
            "max_output_tokens": self.limits.max_output_tokens,
            "context_window_tokens": self.limits.context_window_tokens,
            "capacity_source": "operator_configured" if self.limits.context_window_tokens else "unknown",
            "max_context_chars": self.limits.max_context_chars,
            "safety_margin_tokens": self.limits.safety_margin_tokens,
            "budget_version": "utf8-token-budget-v2",
            "output_token_parameter": next((key for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in payload), None),
            "reasoning_effort": payload.get("reasoning_effort"),
        }

    def context_char_ceiling(self) -> int:
        """提供 JSON 构造器的起始上限；最终是否可用必须再按实际请求验证。"""
        return self.limits.max_context_chars or min(200_000, self.input_limit * 3)

    def build_initial_context(
        self, render: Callable[[int], ContextValue], payload_for: Callable[[ContextValue], dict[str, Any]],
    ) -> ContextValue:
        """按完整结构重建上下文直到请求可容纳；不切割 JSON、证据或源码行。"""
        ceiling = self.context_char_ceiling()
        for _ in range(12):
            context = render(ceiling)
            payload = payload_for(context)
            estimate = self.estimate(payload).tokens
            reserve = min(2048, max(256, self.input_limit // 5))
            if estimate <= self.input_limit - reserve and (
                not self.limits.max_context_chars or len(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ) <= int(self.limits.max_context_chars * 0.9)
            ):
                return context
            # 留出一定工具续接空间；仅收缩完整证据构造预算，不改变模型/输出设置。
            ratio = min(0.8, self.input_limit / max(1, estimate) * 0.9)
            ceiling = int(ceiling * ratio)
            if ceiling < 256:
                break
        raise ModelBudgetError("Application budget cannot hold a complete initial evidence envelope")
