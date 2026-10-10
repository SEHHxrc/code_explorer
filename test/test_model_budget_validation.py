"""预算验证脚本的离线回归；所有模型响应均为模拟，不读取 .env 或数据库。"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from backend.app.llm.base import ModelProvider, ModelResult, ModelTurn, ToolCall
from backend.app.llm.conversation import ModelMessage
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.llm.settings import ModelLimits
from test.validate_model_budget import validate, validate_output


class BudgetValidationProvider(ModelProvider):
    """通过固定响应验证脚本流程，绝不发起 HTTP。"""

    name = "mock"
    model = "mock"
    model_limits = ModelLimits(0, 4800, max_input_tokens=24000, safety_margin_tokens=1024, tokenizer="utf8")

    def __init__(self, *, valid_markers: bool = True, known_usage: bool = True) -> None:
        self.calls = 0
        self.valid_markers = valid_markers
        self.known_usage = known_usage

    async def generate(self, *, instructions: str, prompt: str) -> ModelResult:
        raise AssertionError("Budget validation must use native requests")

    async def generate_with_tools(self, **kwargs: Any) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            text = kwargs["messages"][0]["content"]
            arguments: dict[str, str] = {}
            for key in ("FIRST", "MIDDLE", "LAST"):
                match = re.search(key + r"=([0-9a-f]+)", text)
                assert match is not None
                arguments[key.lower()] = match.group(1)
            if not self.valid_markers:
                arguments["first"] = "wrong"
            call = ToolCall("probe1", "budget_probe", arguments)
            continuation = ModelMessage(role="assistant", tool_calls=[{
                "id": call.id, "type": "function", "function": {
                    "name": call.name, "arguments": json.dumps(arguments)}}])
            return ModelTurn("", self.name, self.model, (call,), continuation=continuation)
        if self.calls == 2:
            nonce = json.loads(kwargs["messages"][-1]["content"])["probe_token"]
            return ModelTurn(nonce, self.name, self.model,
                             metadata=ModelResponseMetadata(finish_reason="stop"))
        output = "\n".join(f"{index:04d}:ok" for index in range(1, 601))
        metadata = ModelResponseMetadata(finish_reason="stop",
                                         usage=ModelUsage(output_tokens=3001 if self.known_usage else None))
        return ModelTurn(output, self.name, self.model, metadata=metadata)


class ModelBudgetValidationTests(unittest.TestCase):
    """验证成功门槛和早停策略，不把接受参数误报为长输出已验证。"""

    def run_validation(self, provider: BudgetValidationProvider) -> bool:
        with patch("test.validate_model_budget.create_model_provider", return_value=provider), \
             patch("test.validate_model_budget.emit"):
            return asyncio.run(validate())

    def test_success_requires_complete_tool_chain_and_large_actual_usage(self) -> None:
        provider = BudgetValidationProvider()
        self.assertTrue(self.run_validation(provider))
        self.assertEqual(3, provider.calls)

    def test_marker_failure_stops_without_more_requests(self) -> None:
        provider = BudgetValidationProvider(valid_markers=False)
        self.assertFalse(self.run_validation(provider))
        self.assertEqual(1, provider.calls)

    def test_missing_provider_usage_does_not_claim_output_capacity(self) -> None:
        provider = BudgetValidationProvider(known_usage=False)
        self.assertFalse(self.run_validation(provider))

    def test_output_only_uses_one_request_without_long_input(self) -> None:
        """输出验证不再被长输入失败阻断，也不需要调整输入或思考配置。"""
        provider = BudgetValidationProvider()
        provider.calls = 2
        with patch("test.validate_model_budget.emit"):
            self.assertTrue(asyncio.run(validate_output(provider)))
        self.assertEqual(3, provider.calls)

    def test_output_only_rejects_length_termination_even_with_matching_text(self) -> None:
        """完整格式不能掩盖平台报告的截断终止状态。"""
        provider = BudgetValidationProvider()
        text = "\n".join(f"{index:04d}:ok" for index in range(1, 601))
        turn = ModelTurn(text, "mock", "mock", metadata=ModelResponseMetadata(
            finish_reason="length", usage=ModelUsage(output_tokens=3001)))
        with patch.object(provider, "generate_with_tools", new=AsyncMock(return_value=turn)) as send, \
             patch("test.validate_model_budget.emit"):
            self.assertFalse(asyncio.run(validate_output(provider)))
        self.assertEqual(1, send.await_count)

    def test_output_only_accepts_completed_responses_api(self) -> None:
        """Responses 的 completed 与 Chat 的 stop 分别校验，不混用终止字段。"""
        provider = BudgetValidationProvider()
        text = "\n".join(f"{index:04d}:ok" for index in range(1, 601))
        turn = ModelTurn(text, "mock", "mock", metadata=ModelResponseMetadata(
            response_status="completed", usage=ModelUsage(output_tokens=3001)))
        with patch.object(provider, "generate_with_tools", new=AsyncMock(return_value=turn)), \
             patch("test.validate_model_budget.emit"):
            self.assertTrue(asyncio.run(validate_output(provider)))


if __name__ == "__main__":
    unittest.main()
