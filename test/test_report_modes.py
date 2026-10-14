"""最终响应分类、阶段预算及显式收尾配置回归；全部使用模拟模型。"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.orchestrator import AgentRunManager
from backend.app.agents.reporting import execution_policy
from backend.app.experiments.audit.policy import contract_digest
from backend.app.experiments.report import assess_answer
from backend.app.llm.base import ModelTurn, ToolCall, ModelOutputError
from backend.app.llm.conversation import validate_messages
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.llm.settings import get_final_report_mode
from test.probe_final_report_modes import fixed_history
from test.test_agent_foundation import MemoryRunStore, sample_artifact
from test.test_experiment_quality import ANSWER


class ReportModeTests(unittest.TestCase):
    def test_tool_finish_without_payload_is_not_an_actual_tool_call(self):
        for text in ("", "继续读取更多文件", ANSWER):
            turn = ModelTurn(text, "mock", "m", metadata=ModelResponseMetadata(finish_reason="tool_calls", usage=ModelUsage(10, 5, 15)))
            with self.subTest(text=text), self.assertRaises(ModelOutputError) as failure:
                AgentRunManager._require_answer(turn, forbid_tools=True, require_report=True)
            self.assertEqual("tool_finish_without_payload", failure.exception.code)
            self.assertEqual("tool_calls", turn.metadata.finish_reason)
            self.assertEqual(15, turn.metadata.usage.total_tokens)

    def test_investigation_text_is_distinct_from_blank_and_tool_violation(self):
        turn = ModelTurn("需要继续读取代码再判断。", "mock", "m", metadata=ModelResponseMetadata(finish_reason="stop"))
        with self.assertRaises(ModelOutputError) as failure:
            AgentRunManager._require_answer(turn, forbid_tools=True, require_report=True)
        self.assertEqual("final_report_contract_not_met", failure.exception.code)
        self.assertEqual(turn.text, AgentRunManager._require_answer(turn))  # 普通对话不强制实验格式。

    def test_unknown_or_truncated_finish_is_not_rewritten(self):
        for metadata in (ModelResponseMetadata(), ModelResponseMetadata(finish_reason="length")):
            turn = ModelTurn(ANSWER, "mock", "m", metadata=metadata)
            self.assertEqual(ANSWER, AgentRunManager._require_answer(turn, forbid_tools=True, require_report=True))
            self.assertNotEqual("complete", assess_answer(ANSWER, metadata, require_report=True)["status"])

    def test_without_definitions_keeps_valid_paired_history_for_both_adapters(self):
        history = fixed_history()
        for provider in (OpenAICompatibleProvider(provider_name="compatible", base_url="http://mock", model="mock"),
                         OpenAIResponsesProvider(api_key="fake", model="mock")):
            payload = provider.request_payload(instructions="final", messages=history, tools=[], tool_choice="none")
            self.assertNotIn("tools", payload)
            self.assertNotIn("tool_choice", payload)
            self.assertIn("fixture-read", str(payload))
            self.assertIn("APPLICATION_STAGE", str(payload))
        validate_messages(history)

    def test_modes_and_rounds_change_frozen_contract(self):
        self.assertNotEqual(contract_digest("prompt", [], "v", execution_policy(4, "tools_none")),
                            contract_digest("prompt", [], "v", execution_policy(4, "history_no_tools")))
        self.assertNotEqual(contract_digest("prompt", [], "v", execution_policy(4, "tools_none")),
                            contract_digest("prompt", [], "v", execution_policy(6, "tools_none")))
        with patch.dict(os.environ, {"CODE_EXPLORER_LLM_FINAL_REPORT_MODE": "auto_fallback"}):
            with self.assertRaises(ValueError):
                get_final_report_mode()

    def test_phases_remaining_rounds_and_no_hidden_fallback(self):
        first = ModelTurn("", "mock", "m", (ToolCall("one", "list_entrypoints"),))
        second = ModelTurn("", "mock", "m", (ToolCall("two", "list_entrypoints"),))
        final = ModelTurn("回答", "mock", "m", metadata=ModelResponseMetadata(finish_reason="stop"))
        provider = SimpleNamespace(name="mock", model="m", generate_with_tools=AsyncMock(side_effect=[first, second, final]))
        store = MemoryRunStore()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODE_EXPLORER_LLM_FINAL_REPORT_MODE": "history_no_tools"}), \
             patch("backend.app.agents.orchestrator.create_model_provider", return_value=provider):
            asyncio.run(AgentRunManager(store=store)._run(run_id="run", project_id="p", user_id="u", project_root=directory,
                artifact=sample_artifact(), request=AgentRunRequest(question="q", max_steps=2)))
        calls = provider.generate_with_tools.call_args_list
        self.assertEqual(3, len(calls))
        self.assertIn("剩余工具轮次：2", calls[0].kwargs["messages"][-1]["content"])
        self.assertIn("剩余工具轮次：1", calls[1].kwargs["messages"][-1]["content"])
        self.assertEqual([], calls[2].kwargs["tools"])
        self.assertEqual("none", calls[2].kwargs["tool_choice"])
        self.assertIn("最终报告阶段", calls[2].kwargs["messages"][-1]["content"])
        validate_messages(calls[2].kwargs["messages"])
        self.assertEqual("completed", store.status)

    def test_forbidden_tool_result_does_not_trigger_an_extra_request(self):
        first = ModelTurn("", "mock", "m", (ToolCall("one", "list_entrypoints"),))
        final = ModelTurn("", "mock", "m", metadata=ModelResponseMetadata(finish_reason="tool_calls", usage=ModelUsage(10, 5, 15)))
        provider = SimpleNamespace(name="mock", model="m", generate_with_tools=AsyncMock(side_effect=[first, final]))
        store = MemoryRunStore()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODE_EXPLORER_LLM_FINAL_REPORT_MODE": "history_no_tools"}), \
             patch("backend.app.agents.orchestrator.create_model_provider", return_value=provider):
            asyncio.run(AgentRunManager(store=store)._run(run_id="run", project_id="p", user_id="u", project_root=directory,
                artifact=sample_artifact(), request=AgentRunRequest(question="q", max_steps=1)))
        self.assertEqual(2, provider.generate_with_tools.call_count)
        self.assertEqual("failed", store.status)
        failure = next(value for kind, value in store.events if kind == "run.failed")
        self.assertEqual("tool_finish_without_payload", failure["error_code"])
        self.assertEqual(0, failure["output_diagnostics"]["returned_tool_calls"])
        self.assertEqual("tool_calls", failure["final_response_metadata"]["finish_reason"])


if __name__ == "__main__":
    unittest.main()
