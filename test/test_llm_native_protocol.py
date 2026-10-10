"""原生会话与标准库 Token 估算回归测试；不访问真实模型和历史记录。"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from backend.app.agents.conversation_window import ToolRound, render_conversation_window
from backend.app.llm.budget import BudgetPlanner, ModelBudgetError, request_payload
from backend.app.llm.conversation import ModelMessage, validate_messages
from backend.app.llm.diagnostics import probe_model_connection
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.registry import ModelLimits, get_model_limits
from backend.app.llm.tokens import TokenCounter


def sample_round(call_id: str = "c1", text: str = "1: value = input()") -> ToolRound:
    """构造包含一个调用和精确源码位置的完整测试轮次。"""
    return ToolRound(ModelMessage(role="assistant", content=None, tool_calls=[{
        "id": call_id, "type": "function", "function": {"name": "read_file_range", "arguments": "{}"},
    }]), [{"call_id": call_id, "name": "read_file_range", "arguments": {"path": "main.py"},
            "result": {"content": {"path": "main.py", "start_line": 1, "end_line": 1, "text": text},
                       "evidence": [{"path": "main.py", "line": 1}], "truncated": False}}])


class NativeProtocolTests(unittest.TestCase):
    """校验协议与窗口的公共安全不变量。"""

    def test_rejects_orphan_duplicate_and_missing_results(self) -> None:
        """不能让压缩产生孤立工具结果、重复调用或无结果调用。"""
        initial = ModelMessage(role="user", content="question")
        assistant = sample_round().assistant
        tool = ModelMessage(role="tool", tool_call_id="c1", content="result")
        validate_messages([initial, assistant, tool])
        for messages in ([tool], [initial, assistant], [initial, assistant, tool, tool],
                         [initial, assistant, initial], [initial, assistant, tool, assistant, tool]):
            with self.assertRaises(ValueError):
                validate_messages(messages)

    def test_chat_continuation_preserves_reasoning_without_persisting_it(self) -> None:
        """保留供应商交错思考续接信息，但其不属于响应元数据或源码证据。"""
        async def scenario() -> None:
            provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock")
            response = {"choices": [{"message": {"content": None, "reasoning_content": "private state",
                                                  "tool_calls": sample_round().assistant["tool_calls"]}}]}
            with patch("backend.app.llm.providers.compatible_provider.post_json", new=AsyncMock(return_value=response)) as send:
                first = await provider.generate_with_tools(instructions="safe", prompt="question", tools=[])
                self.assertEqual("private state", first.continuation["reasoning_content"])
                messages = [ModelMessage(role="user", content="question"), first.continuation,
                            ModelMessage(role="tool", tool_call_id="c1", content="result")]
                await provider.generate_with_tools(instructions="safe", messages=messages, tools=[])
                wire = send.await_args.args[1]["messages"]
                self.assertEqual(["system", "user", "assistant", "tool"], [item["role"] for item in wire])
                self.assertEqual("c1", wire[-1]["tool_call_id"])
                self.assertEqual("private state", wire[-2]["reasoning_content"])
                self.assertNotIn("reasoning_content", first.metadata.__dict__)
        asyncio.run(scenario())

    def test_responses_replays_original_reasoning_and_outputs(self) -> None:
        """Responses 续接必须携带原 output 项及 function_call_output。"""
        async def scenario() -> None:
            provider = OpenAIResponsesProvider(api_key="mock", model="mock")
            output = [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "state"},
                      {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "read_file_range", "arguments": "{}"}]
            with patch("backend.app.llm.providers.openai_provider.post_json", new=AsyncMock(return_value={"output": output})):
                first = await provider.generate_with_tools(instructions="safe", prompt="question", tools=[])
            messages = [ModelMessage(role="user", content="question"), first.continuation,
                        ModelMessage(role="tool", tool_call_id="c1", content="result")]
            wire = provider.request_payload(instructions="safe", messages=messages, tools=[])
            self.assertEqual(output, wire["input"][1:3])
            self.assertEqual("function_call_output", wire["input"][-1]["type"])
            self.assertFalse(wire["store"])
        asyncio.run(scenario())

    def test_output_parameter_is_explicit_and_unknown_parameters_are_omitted(self) -> None:
        """第三方参数不能按模型别名猜测；默认不强制 temperature/thinking。"""
        provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock",
                                             output_token_parameter="max_completion_tokens", reasoning_effort="low")
        wire = provider.request_payload(instructions="safe", messages=[ModelMessage(role="user", content="q")], tools=[])
        self.assertEqual(2400, wire["max_completion_tokens"])
        self.assertNotIn("max_tokens", wire)
        self.assertNotIn("temperature", wire)
        self.assertNotIn("thinking", wire)
        self.assertEqual("low", wire["reasoning_effort"])

    def test_native_window_keeps_all_results_for_every_retained_round(self) -> None:
        """压缩后仍保留所有调用的结果、定位及证据，原始结果不被修改。"""
        provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock")
        planner = BudgetPlanner(ModelLimits(0, 2400, max_input_tokens=2200), model="mock")
        rounds = [sample_round("old", "\n".join(f"{i}: old_value" for i in range(100))),
                  sample_round("new", "\n".join(f"{i}: new_value" for i in range(100)))]
        messages, stats = render_conversation_window(rounds, initial=ModelMessage(role="user", content="q"),
                                                      instructions="safe", tools=[], planner=planner, provider=provider)
        validate_messages(messages)
        self.assertEqual("new", messages[-1]["tool_call_id"])
        self.assertEqual([{"path": "main.py", "line": 1}], json.loads(messages[-1]["content"])["evidence"])
        self.assertTrue(planner.fits(provider.request_payload(instructions="safe", messages=messages, tools=[])))
        self.assertGreater(stats["compacted_observations"] + stats["omitted_observations"], 0)
        self.assertEqual(100, len(rounds[-1].observations[0]["result"]["content"]["text"].splitlines()))

    def test_budget_reserves_output_and_reports_unknown_capacity(self) -> None:
        """应用输入限制不是模型窗口；知道窗口时才共同预留输出。"""
        limits = ModelLimits(0, 2400, max_input_tokens=10000)
        unknown = BudgetPlanner(limits, model="unknown")
        self.assertEqual(9488, unknown.input_limit)
        self.assertEqual("unknown", unknown.require({"input": "q"})["capacity_source"])
        known = BudgetPlanner(replace(limits, context_window_tokens=5000))
        self.assertEqual(2088, known.input_limit)
        with self.assertRaises(ModelBudgetError):
            BudgetPlanner(replace(limits, context_window_tokens=2500))

    def test_model_switch_does_not_inherit_default_model_window(self) -> None:
        """切换到另一模型后，原模型配置的容量不得被误报成新模型容量。"""
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MODEL": "one", "CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS": "20000"}, clear=True):
            self.assertEqual(20000, get_model_limits("one").context_window_tokens)
            self.assertIsNone(get_model_limits("two").context_window_tokens)

    def test_unknown_alias_uses_explicit_estimate_not_openai_exact_count(self) -> None:
        """未知 GLM 别名不冒充 OpenAI 分词器；请求统计明确为估算。"""
        counter = TokenCounter(model="FW-GLM-5.3-noklok-1")
        self.assertEqual(len("输入".encode("utf-8")), counter.count("输入"))
        estimate = counter.request({"input": "输入"})
        self.assertEqual("utf8_bytes_estimate", estimate.method)
        self.assertFalse(estimate.exact)

    def test_legacy_tokenizers_are_rejected_without_loading_dependencies(self) -> None:
        """旧配置明确失败，不静默降级，也不再需要安装外部分词库。"""
        for options in ({"tokenizer": "tiktoken:cl100k_base"}, {"tokenizer_path": "tokenizer.json"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                TokenCounter(**options)

    def test_request_count_includes_tools_and_native_history(self) -> None:
        """工具定义和工具结果都属于输入，不能只统计用户问题。"""
        provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock")
        planner = BudgetPlanner(ModelLimits(0, 2400), model="mock")
        initial = ModelMessage(role="user", content="q")
        small = request_payload(provider, instructions="safe", messages=[initial], tools=[])
        group = sample_round()
        messages = [initial, group.assistant, ModelMessage(role="tool", tool_call_id="c1", content="result")]
        large = request_payload(provider, instructions="safe", messages=messages, tools=[{"name": "read", "description": "read", "parameters": {"type": "object"}}])
        self.assertGreater(planner.estimate(large).tokens, planner.estimate(small).tokens)

    def test_failed_second_probe_keeps_first_stage_success(self) -> None:
        """只有发起工具调用不算 Agent 可用；第二轮故障保留阶段信息。"""
        from backend.app.llm.http import ModelEndpointError
        environment = {"CODE_EXPLORER_LLM_PROVIDER": "compatible", "CODE_EXPLORER_LLM_MODEL": "mock",
                       "CODE_EXPLORER_LLM_BASE_URL": "http://mock"}
        first = {"choices": [{"message": {"tool_calls": [{"id": "c1", "type": "function",
                                                          "function": {"name": "agent_compatibility_probe", "arguments": "{}"}}]}}]}
        with patch.dict(os.environ, environment, clear=True), patch("backend.app.llm.providers.compatible_provider.post_json",
                                                                   new=AsyncMock(side_effect=[first, ModelEndpointError(status_code=503)])):
            result = asyncio.run(probe_model_connection())
        self.assertTrue(result["tool_calling_verified"])
        self.assertTrue(result["generation_available"])
        self.assertFalse(result["agent_compatible"])
        self.assertEqual("tool_result", result["probe_stage"])
        self.assertEqual(2, result["requests_sent"])

    def test_plain_generation_cannot_bypass_budget(self) -> None:
        """非 Agent 的普通文本接口同样必须在发送前拒绝超预算请求。"""
        async def scenario() -> None:
            environment = {"CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "1000"}
            with patch.dict(os.environ, environment, clear=True):
                provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock")
            with patch("backend.app.llm.providers.compatible_provider.post_json", new=AsyncMock()) as send:
                with self.assertRaises(ModelBudgetError):
                    await provider.generate(instructions="safe", prompt="x" * 2000)
                send.assert_not_called()
        asyncio.run(scenario())

    def test_invalid_probe_configuration_sends_no_request(self) -> None:
        """输出参数配置无效时返回安全诊断，不向上游发送请求。"""
        environment = {"CODE_EXPLORER_LLM_PROVIDER": "compatible", "CODE_EXPLORER_LLM_MODEL": "mock",
                       "CODE_EXPLORER_LLM_BASE_URL": "http://mock"}
        with patch.dict(os.environ, environment, clear=True), patch(
            "backend.app.llm.diagnostics.create_model_provider", side_effect=ValueError("invalid option")
        ), patch("backend.app.llm.providers.compatible_provider.post_json", new=AsyncMock()) as send:
            result = asyncio.run(probe_model_connection())
        self.assertEqual("invalid_model_options", result["error_code"])
        self.assertEqual(0, result["requests_sent"])
        self.assertFalse(result["agent_compatible"])
        send.assert_not_called()

    def test_provider_freezes_budget_at_construction(self) -> None:
        """运行中环境变化不偷偷改变同一 Provider 的实际发送预算。"""
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "3000"}, clear=True):
            provider = OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock")
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_MAX_INPUT_TOKENS": "9000"}, clear=True):
            self.assertEqual(3000, provider.model_limits.max_input_tokens)

    def test_explicit_context_error_is_not_retried_even_when_gateway_reports_503(self) -> None:
        """明确平台超窗与普通 503 分开，不反复提交必然超限请求。"""
        from backend.app.llm.http import ModelEndpointError
        error = ModelEndpointError(status_code=503, error_code="context_length_exceeded")
        self.assertFalse(error.retryable)
        self.assertIn("上下文超限", error.public_message)

    def test_estimation_is_model_independent_and_counts_multibyte_text(self) -> None:
        """两组均用相同估算，中文、表情与工具 JSON 都按实际 UTF-8 表示计数。"""
        payload = {"messages": [{"role": "user", "content": "输入🙂"}], "tools": [{"name": "read"}]}
        expected = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        for model in ("gpt-5", "FW-GLM-5.3-noklok-1", "local"):
            estimate = TokenCounter(model=model).request(payload)
            self.assertEqual(expected, estimate.tokens)
            self.assertEqual("utf8_bytes_estimate", estimate.method)
            self.assertFalse(estimate.exact)


if __name__ == "__main__":
    unittest.main()
